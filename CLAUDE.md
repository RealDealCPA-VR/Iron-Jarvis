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

1. Bump the version in **six locations, with ANCHORED edits — run
   `uv run python scripts/bump_version.py OLD NEW`** (never blanket
   search/replace — it once rewrote a dependency pin): `pyproject.toml`
   (`version = `), `src/iron_jarvis/__init__.py` (`__version__`),
   `desktop/package.json` (`"version"`). Plus `extensions/chrome/manifest.json` and
   `extensions/chrome/package.json` (`"version"`), `docs/HANDBOOK.md` on its
   `Current as of vX` line ONLY, and `uv lock` regenerated. The script refuses
   unless each anchor matches exactly once. THIS IS NOT OPTIONAL: v1.257.0 and
   v1.258.0 used ad-hoc blanket replaces that relabelled five lines of shipped
   Handbook history (fixed in v1.258.1).
2. Commit + push to master. **IF THE COMMIT TOUCHES `.github/workflows/*`, PUSH
   THE TAG YOURSELF** (`git tag vX.Y.Z <sha> && git push origin vX.Y.Z`) — CI's
   `GITHUB_TOKEN` is refused when it pushes a tag whose commit changes a
   workflow file ("refusing to allow a GitHub App to create or update
   workflow … without `workflows` permission"; no `permissions:` key grants
   it). v1.260.0's master-push Release died there after a green suite, and the
   tag push from the user's own credentials is what shipped v1.261.0 (a tag
   push triggers Release on its own — cancel the master-push run first or two
   runs publish the same version). CI (`.github/workflows/release.yml`) detects the
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

- **Define the done condition before you edit, and report four things when you
  deliver.** Borrowed from omg.dev's agent conventions (2026-09-12 review) because
  this repo's rules are mostly post-mortems and it lacked the preflight half.
  Before touching a file, write down in one line what "done" means for THIS change
  (the measured number, the test that must go red-then-green, the surface a user
  will see). When delivering, report exactly: (1) BEHAVIOUR — what changed for the
  user, in plain words; (2) VERIFICATION — which suites/mutations/measurements ran,
  with numbers read from files, never exit codes; (3) DELIVERY STATE — one of
  `in the tree` / `committed` / `pushed` / `CI green` / `installer published`, and
  never a higher one than the evidence supports (a green local build is not a
  deployment; a release object with zero assets is not a ship); (4) RISK — what
  could still be wrong and what would show it. A report missing any of the four
  is incomplete, and the delivery-state ladder is the one this project has
  already paid for: v1.251.0, v1.254.0, v1.255.0 and v1.258.1 each had a moment
  where "pushed" was reported in a way that read as "shipped".

- **The browser add-on is proven in the browser the user actually has, against
  an ISOLATED daemon — never by loading a bundle that dials 8787.** v1.259.0:
  this PC has no Chrome; it has Edge 153, and "Chrome or Edge" had been a claim
  in the docs, not a proof. The production bundle dials `ws://127.0.0.1:8787` —
  the user's live daemon — and a newer browser connection REPLACES the real
  pairing, so a test load in a second browser would have knocked the user's own
  browser off. `extensions/chrome/esbuild.config.mjs` is the add-on's ONE build
  path and takes dev-only overrides (`IJ_ADDON_OUT`, `IJ_ADDON_DAEMON_WS`,
  `IJ_ADDON_JARVIS_URL`) that it REFUSES unless loopback; the bundle reads the
  injected constant directly so each build carries exactly one daemon address.
  `scripts/verify_browser_addon.sh` does the whole proof — dev bundle, scratch
  daemon on 8797, headless browser with a throwaway profile on a fictional data:
  URL, pairing through the isolated API, a read-only round trip — and prints the
  paired browser's NAME, which the add-on now sends on `browser.hello` and the
  card renders ("Paired browser: Microsoft Edge 153"; absent = unknown, never
  assumed to be Chrome). Both `chrome://extensions` and `edge://extensions` are
  quoted as addresses in the setup copy; the vocabulary scrubs in
  `browser-card-v1235` and `browser-setup-modal-v1240` strip both for the same
  reason (neither names the add-on). AND: `extensions/chrome/src/protocol.ts` is
  GENERATED from `browser/protocol.py` (`uv run python -m
  iron_jarvis.browser.gen_protocol`) — a hand edit there is caught by
  `test_browser_protocol_v1235`, which cost this release a round; add the field
  to the TypedDict and regenerate.
  **"CHROME (IN EDGE: …)" IS STILL A CHROME PAGE** (v1.261.0). The user, on an
  Edge-only work PC: "I only get the instructions for Chrome, not Edge." Every
  step is now written for ONE browser: `GET /browser/status` carries
  `installed_browsers` (doctor `installed_browsers()`, cached per process —
  the card polls every 5 s), `dashboard/components/browser/browserWords.ts`
  holds the words per browser (its own add-ons page, its own pin gesture) and
  `pickBrowser` makes the one decision — the paired browser, else the only
  installed one, else Chrome — with a remembered override in the window's
  header. Do not put a second browser back in parentheses; add it to
  `BROWSERS` and let the pick choose. `browser-setup-edge-v1261.test.tsx`
  asserts an Edge-only window contains no "Chrome" outside the toggle.

- **Updates come from the `updates` branch manifest first; GitHub's releases
  feed is the FALLBACK, and no raw HttpError ever reaches the Updates page**
  (v1.260.0). electron-updater's GitHub provider opens every check by reading
  `github.com/<repo>/releases.atom`, which GitHub renders in 2–10 s and cuts at
  ~10 s — on 2026-09-14 about every other check on both of the user's PCs 504'd,
  and `friendlyUpdateError` (which knew only the 404 publishing window) printed
  the whole error page plus the `_gh_sess` cookie on the Updates page. Pruning
  331 old releases did NOT change the feed's timing (the count was a wrong
  hypothesis; tags were kept). So: `release.yml` publishes electron-builder's
  `latest.yml` with ABSOLUTE installer URLs (`scripts/publish_update_manifest.py`
  — a line rewrite that refuses a wrong version or a missing sha512) alone on the
  orphan `updates` branch, AFTER `--draft=false`; `desktop/main.js` reads it via
  the generic provider (`UPDATE_MANIFEST_URL`) and `checkForUpdatesWithFallback`
  swaps to `UPDATE_GITHUB_FEED` only when that fails, restoring the manifest for
  the next check. electron-updater EMITS "error" before it rejects, so
  `onUpdaterError` is suppressed while the manifest attempt is armed — the feed
  attempt reports. `updateErrorKind` sorts failures into publishing / transient /
  other; a transient one is one sentence and arms ONE `UPDATE_RETRY_MS` re-check.
  Both workflows trigger on master + tags only, so the `updates` push never
  loops. `tests/test_update_channel_v1260.py` lifts the functions and runs them
  under node; keep owner/repo in `main.js` in step with `desktop/package.json`.

- **The sidebar is armed BY SURFACE, and a browser-agent turn stays in chat**
  (v1.262.0). The user's report: the add-on "doesn't navigate and control the
  browser and simply acts as a chat bot next to the window." The daemon had
  every acting tool; the sidebar could not reach them: a panel turn was armed by
  the SENTENCE (autoselect rules like "click the 'Buy' button"), six rounds
  ended in an escalation the sidebar cannot follow, and the prompt never said it
  could act. Now `browser/panel.py` passes `arm_family=<the browser_* ceiling>`
  to `stream_chat_turn`, and the stream lane ARMS the read tier and ASK-ARMS the
  acting tier by each tool's `min_access` — visible, never granted: every page
  action still pauses for the card — then runs the access gate and the ceiling
  exactly as before. `_BROWSER_TOOL_ROUNDS` (24) applies when a page-acting tool
  is armed; `_stays_in_chat` (office OR browser-agent) ends the turn in chat at
  the last round with `_out_of_rounds_instruction`; `BROWSER_AGENT_BLOCK` rides
  the Tools seam of BOTH lanes only when acting tools are armed (a brief that
  says "you can click" beside no click tool is a lie), and `_browser_section`
  adds `BROWSER_LOOK_ONLY_LINE` at read_only. The panel's `approve` takes
  `scope: "task"` → the chat lane's `conversation` grant (the rest of THIS turn;
  every Send is a fresh turn), and its tool/approval frames are worded by
  `describe_browser_call`. Do not put a browser tool back behind a sentence
  rule for the panel, and do not widen a plain Allow to more than one call.
  `tests/test_browser_agent_v1262.py` (harness: `tests/_fakes/panel_harness.py`).

- **The reasoning level is ONE vocabulary, ONE table, and applied only where
  the serving model offers it** (v1.263.0). `providers/reasoning.py` says which
  (provider, model) pairs take `low`/`medium`/`high`; `GET /models` rows carry
  `reasoning: [...]` so the composer draws the control only there;
  `ChatBody.reasoning` rides both lanes as `**_reasoning_kw(body)` — PASSED ONLY
  WHEN SET, because 18 router doubles predating the knob take no `**kw`; the
  router's `_applied_reasoning(adapter, level)` decides per SERVING adapter
  (failover candidates get none) and reports it on `RouteResult.reasoning` /
  the final frame → the `route` object → the receipt's quiet "reasoning high".
  Every adapter accepts `reasoning: str = ""` beside the guided knobs and
  forwards it only when set; the translations: OpenAI `reasoning_effort` (+ one
  retry without it on a 400 that names it), Responses `reasoning.effort`,
  Anthropic `thinking` budgets with `max_tokens` raised to fit AND `raw_blocks`
  replayed verbatim (a thinking tool loop refuses a rebuilt assistant turn),
  Gemini `thinkingConfig.thinkingBudget`, `claude --effort`, `codex -c
  model_reasoning_effort=`. Never add a vendor spelling without a row in the
  table; never send a level the table did not offer.

- **A remote agent is a CONVERSATION, both ways** (v1.285.0). The user: "what
  is stopping me from interacting with my remote agents the way I can with
  Slack?" — `RemoteAgentRegistry.run` was one POST carrying only the task text.
  Now `run(..., history=, conversation_id=, reply_to=)`: the OpenAI dialects
  get real prior turns (`messages` / list-form `input`), `http-task` gets
  `conversation_id` + `history` + `reply_to` BESIDE `task` and a bare `{"task"}`
  when none is given (older endpoints see the v1.157.0 body byte for byte); a
  `202` or `{"accepted": true}` with no result answers `{ok, accepted}` and the
  round records "working — will report back" (`pending: True` on the entry).
  `AgentThreads.remote_history` renders the exchange for the remote (user as
  user, its own lines unprefixed, everyone else named); `_speak_remote` returns
  `(reply, extra)`. INWARD: `RemoteAgentRecord.inbound_enabled /
  inbound_secret_name / inbound_url` (additive), `enable_inbound` mints a
  `token_urlsafe(32)` into the vault and returns it ONCE (re-enable ROTATES),
  `verify_inbound` is constant-time via `auth.token_matches` and FAIL-CLOSED;
  `POST /agents/remote/{name}/inbound` is token-EXEMPT in `auth._is_exempt`
  (exactly the `/inbound` leaf — `/inbound/enable|disable` stay guarded) and
  verified in the handler (inbound off → 403, wrong token → 401, not a
  participant of that room → 403, 60/min → 429, 12k chars → 413); files land
  under `<home>/remote-inbox/<agent>/` through `agents/remote_files` (the same
  trust boundary as the delegate tool); the line is a room entry (`inbound:
  True`, `kind`, `documents`) → `AGENT_THREAD_UPDATED` + `REMOTE_MESSAGE`, and
  a room bound to a DAEMON-owned (phone) thread mirrors it there
  (`CommThreadStore.append(extra=)` whitelists `panelWho/panelKind/documents`)
  and sends it to the phone through `d.inbound_poller`. THE PHONE:
  `InboundPoller._remote_addressee` (after the user append in `_handle_chat`)
  routes "@<remote>" — or a follow-up while `setup_json.addressee` is
  `remote:<name>` — to `_handle_remote_chat`: `AgentThreads.for_chat(thread.id)`
  + `run_round(directed=[name], chat_history=store.history_rows(...))` with a
  `SimpleNamespace(platform=)` shim as `d`, the reply appended attributed and
  sent as "<name>: …"; "@jarvis" clears it (`BACK_TO_JARVIS_REPLY`). A LOCAL
  agent named from the phone stays on the Jarvis lane. DASHBOARD: the chat
  page mirrors inbound room entries (`mirrorRoomInbound`, dedupe by `panelAt`,
  on `agent_thread.updated`/`remote.message` for `roomOf(messages)` and on
  open); `panelKind: progress` is a quiet line; `SetupCard`'s remote row has
  Let it message back / Rotate token / Turn off (token shown once, `#inbound-
  minted`). `app.state.d = d` exists so a test can put a poller double where
  the route looks. Pins: `tests/test_remote_conversation_v1285.py`,
  `dashboard/__tests__/remote-inbound-v1285.test.tsx`,
  `dashboard/__tests__/chat-remote-live-v1285.test.tsx`.
  REVIEW ROUND, each a real defect in the first cut: (1) the packaged daemon
  binds `--host 127.0.0.1` (desktop/main.js) and `auth._host_ok` admits
  loopback Hosts only unless `IRONJARVIS_HOST_ALLOWLIST` is set — so the
  inbound door is unreachable from another machine BY DEFAULT; `enable`
  derives the URL from this machine's LAN address (`_lan_address`, a UDP
  route probe) and answers `reachable: {host_allowed, note}` from the SAME
  `_host_ok`, and the token box + Handbook say the two knobs — never open a
  bind on the user's behalf. (2) `CommThreadStore.history_body` used to hand
  a remote's `panelWho` line to the Jarvis turn as Jarvis's OWN prior turn;
  it now prefixes `agent_line_label(name)` — the exact sentence
  `toRequestMessages` uses, so both lanes tell the model the same thing.
  (3) `rec.enabled` gates inbound too. (4) `remote_agent.inbound.<name>` —
  the vault key uses a "." the remote-name rule cannot produce, because
  `remote_agent_inbound_hermes` collided with the OUTBOUND key of a remote
  named `inbound_hermes`. (5) Unknown room and foreign room both answer 403
  (no id oracle); the response carries file NAMES, not the user's paths;
  `safe_path(inbox, …)` is the second lock; `_fetch` streams and aborts past
  the cap; file posts 10/min + a 512 MB/day inbox budget per agent; phone
  pushes 20 per 10 min (the thread keeps every line). (6) `mirrorRoomInbound`
  captures `chatGenRef` and re-checks `roomOf(current)` after the fetch and
  is single-flight per room. (7) "@jarvis <question>" clears the sticky AND
  falls through to the Jarvis turn (`_words_beyond_mentions`); a remote named
  in the same text wins. (8) `remote_history` skips `pending` notes (Jarvis's
  words about the remote, not the remote's).

- **An @-mentioned agent is shown the CHAT, stays addressed, and hands WORK to
  a session** (v1.284.0). The user: "i need to continually use the @ …
  it basically starts up with no memory of the previous conversation …
  asked for a PDF and it only answered in text". Four defects, one wave.
  (1) `POST /chat/panel` takes `history` (`{who, content}`: `user` |
  `jarvis` | a participant key) and `AgentThreads.run_round(chat_history=)`
  renders it as the speaker's transcript — `chat_transcript` fits it to the
  speaker's window (`_transcript_budget` reads the SAME `_context_window`
  ladder the chat lanes use; newest kept, the drop COUNTED and returned as
  `context.chat_dropped`, shown under the reply as `panelNote`). No
  `history` (the Agents page's `/say`) keeps the room's transcript
  byte-identical. (2) A new chat has no thread id until its first save, so
  round one bound a room to `""` and round two opened a second: the page sends
  `panel_thread_id` (the room the last reply carried) and `for_chat(adopt=)`
  binds an UNBOUND room — never one bound to another chat. (3) `mentions` on
  the body names addressees OUTSIDE the text (`_mentioned(extra=)`), so the
  page's sticky `addressee` (participant keys; `addresseeOf` restores it from
  the saved messages; the `#addressee-strip` + "Back to Jarvis"; the box
  reads "Message builder…") sends follow-ups verbatim. The Jarvis lanes send
  panel replies through `toRequestMessages`, LABELLED as the agent's — a plain
  message after a round used to make Jarvis answer as if it had said them.
  (4) A panelist has `tools=[]` by design (`PANEL_NO_TOOLS`); when ONE local
  agent is addressed and `needs_hands(message)` (the chat lanes' own
  `select_auto_tools` ∩ `_CHANGE_TOOLS`, scored with the @-address STRIPPED —
  the imperative test wants the verb first) is non-empty, or `hands: true`
  (attachments; the "Have builder do this" chip), the route answers
  `mode: "session"` + the roster `target` and the page opens the SAME tooled
  lane a chat escalation opens (`sendAgent(agentType=)`), the reply attributed
  via `awaitingWhoRef` and its files through the run result. `hands: false`
  forces a round; two agents or a remote stay a round. Agents page: `jobTask`
  appends the thread's recent entries under the composer text (the receipt
  says the count read off the body — the old "only the text you typed" line is
  gone) and `JobOutcome` polls the job and renders `SessionFiles` under the
  receipt. Do NOT add a second "work words" list beside `needs_hands`, and do
  not put the chat history into the ROOM's stored messages (the room shows the
  Agents page what happened there). Pins: `tests/test_chat_panel_v1284.py`,
  `dashboard/__tests__/chat-sticky-agent-v1284.test.tsx`,
  `dashboard/__tests__/thread-dispatch-context-v1284.test.tsx`.

- **A module pops out into its own window, on the other screen when there
  is one** (v1.283.0, pop-out windows). `desktop/main.js` keeps ONE
  BrowserWindow per dashboard route (`popouts` Map; `openPopout` focuses an
  existing one), with the main window's chrome (hidden title bar + native
  overlay, the preload, the token safety net, the external-link guards, a
  per-window `did-fail-load` retry) and NONE of its tray behaviour (no close
  interception; `window-all-closed` stays a no-op; Quit closes them all).
  Placement is `windowState.popoutPlacement(saved, displays, mainBounds,
  offset)` — pure, electron-free, node-tested: the saved rectangle while its
  display exists, else centred on the screen that does NOT hold the main
  window, else cascaded off it; bounds remembered per route in
  `popout-windows.json` (`loadPopoutBounds`/`savePopoutBounds`).
  `normalizePopoutPath` keeps the ironjarvis:// path rules and drops the
  query; "/" never pops out. IPC `popout:open|list|focus|close`, every
  handler `isTrustedDashboardSender`-checked. The renderer learns it is a
  pop-out from the PRELOAD (`--ij-popout=<path>` → `ironjarvis.popout.
  isPopout/path`) — a `?popout=1` would not survive in-window navigation.
  Dashboard: `lib/desktopShell.popoutBridge()/isPopoutWindow()`;
  `lib/nav.labelForPath` (the title bar's rule, now shared); TitleBar's
  `#popout-open` door (desktop, not in a pop-out, never for "/"), the
  `#popout-badge` and the window title via `<html data-ij-title>` (the
  NotificationBell prefixes its count onto that base); Sidebar rows'
  `#popout-row-<route>` hover doors; `DesktopNotifyBridge` stays quiet in a
  pop-out (each window holds its own events socket — one toast per event).
  NOT attached to pop-outs: the renderer watchdog (module-global state; the
  main window keeps it) and `installDashboardReloadOnFailure` (same). Two
  windows on the SAME chat thread both autosave — the thread PUT's
  `updated_at` check is the only guard. Pins:
  `tests/test_desktop_popout_v1283.py`, `dashboard/__tests__/popout-v1283.test.tsx`.

- **A kept preference is said on the receipt, in the user's words**
  (v1.282.0, /goal memory wave 2). `remember_preference`'s `data` carries
  `text`; `chat_turn.remembered_from_result(name, result)` reads it (or the
  older `remembered preference: …` output) for a SUCCESSFUL call; BOTH lanes
  append it to `remembered` inside their `if ran:` block (the tools_used
  gate) and carry `"remembered"` ALWAYS (possibly `[]`) on the done frame /
  POST response, like doors — lock-step. The hook decodes it (whitelist!),
  the page stores it on the message (`ChatMessage.remembered`, both lanes'
  receipts) and `TurnReceipt` puts `Remembered: …` on the COLLAPSED line in
  the accent (`#turn-remembered`) — visible without expanding, because a
  write about the user must never be silent. Pins:
  `tests/test_remembered_receipt_v1282.py`,
  `dashboard/__tests__/turn-receipt-remembered-v1282.test.tsx`.

- **A Build pane outside any project can make one in one press** (v1.281.0,
  /goal surfaces wave 2). `PaneChat` shows `#pane-chat-make-project` beside
  the folder name when `projectForCwd` finds nothing; `makeProject` POSTs
  `/projects {name: <folder>, root: cwd}` (the chat page's own promotion)
  and sets the chip, so the next turn carries `project_id` and the assist
  route's `project_for_path` finds it by path. The daily driver's two Build
  panes sit in a folder no project covers — this is the door. Pins:
  `dashboard/__tests__/pane-chat-project-v1281.test.tsx`.

- **The Build assist carries the pane, the profile, the project and the
  lessons; dead pane keys are pruned; the job card remembers its target**
  (v1.280.0, /goal surfaces wave). `POST /terminals/{id}/ai` was the one
  model call with no user context (two sentences + skills + other panes'
  output). Now, in the chat lanes' order: `_pane_identity_block(session)`
  (name, folder, CLI), `_profile_section(platform)`, `project_context_block(
  project_for_path(engine, session.cwd))` and `learning.apply_to_prompt` —
  each "" when there is nothing. `projects/locate.py` is the daemon-side
  twin of the dashboard's `projectForCwd` (most specific ACTIVE root at a
  segment boundary, case-folded; never raises). `components/terminal/
  paneKeys.ts::prunePaneStorage(liveIds)` removes `ij.pane.view.<id>`,
  `ij.pane.thread.<id>` and `ij_term_layout` entries for panes the daemon no
  longer offers — called ONLY after a successful `/terminals` answer (a
  booting daemon answers 503, never an empty list). `JobPostCard` opens on
  `ij_agents_last_target` while the roster still lists it (`effectiveTarget`
  falls back to the Team visibly otherwise). Pins:
  `tests/test_build_context_v1280.py`, `pane-keys-v1280.test.ts`,
  `job-target-v1280.test.tsx`.

- **Task reflections are not knowledge about the user; chat can remember a
  stated preference; the Memory page says what it knows** (v1.279.0, /goal
  memory wave 1). The live ledger: 25 lessons, 23 of them the orchestrator's
  per-run `Worked well for '<task>': <summary>` reflections (weight 1, one
  recording the offline mock's scripted answer), and the top-8 injection under
  "What I've learned about working with you" was six of those. Now
  `LearningEngine.lessons(exclude_sources=)` + `_PROMPT_EXCLUDED_SOURCES =
  ("reflection",)` keep them OUT of `apply_to_prompt` (they still feed
  `dedup`/`distill` and the Lessons tab; `counts_by_source` reports them).
  `remember_preference` had sat in `AUTO_SAFE_TOOLS` since v1.141.0 with NO
  autoselect rule — membership without a rule arms nothing — so "from now on
  keep answers short" reached no memory from chat; a vocabulary rule
  (lasting-instruction shapes: "from now on", "always/never <do>", "I
  prefer", "call me") awards it 7, and `preference_block(armed)` rides the
  Tools seam of BOTH lanes ONLY when the tool is armed (the v1.262.0 rule:
  a brief beside no tool is a lie), lock-step. `memory/overview.py::
  memory_overview(platform)` (never raises; off the loop at
  `GET /memory/overview`) feeds `components/memory/KnowsAboutYou.tsx`, mounted
  at the top of `MemorySurface`; silent when the daemon cannot answer. And
  `_clean_setup` now stores `reasoning` (vocabulary-checked) — the page had
  sent and read it since v1.263.0 while the whitelist dropped it, so the
  level reset on every thread reopen. Pins: `tests/test_memory_knows_you_v1279.py`,
  `dashboard/__tests__/knows-about-you-v1279.test.tsx`; the v1 lesson pin in
  `tests/test_learning.py` moved (a reflection is asserted OUT of the prompt).

- **A named chat turn takes steer notes from any connection, and a sent
  message is editable** (v1.278.0, /goal wave 5). The sidebar's steer
  contract (v1.242.0: `steer_source` consulted at the ROUND BOUNDARY, never
  inside a sentence) is now reachable by name: `TurnHandle` carries a note
  queue (`queue_steer`/`take_steers`), `TURNS.steer(turn_id, text)` /
  `TURNS.take_steers(turn_id)` (unknown or finished id → False/[]),
  `POST /chat/turns/{turn_id}/steer {text}` (404 unknown/finished/empty —
  never a silent success), and `stream_chat_turn` attaches a registry-backed
  source to a NAMED turn that brought none (the sidebar keeps passing its
  own). CONSUMPTION IS STILL THE ONLY SIGNAL: the `round` frame that follows
  a consumed note carries it (`steer`), and NO new frame kind was added. The
  page mints `turn_id` per turn (`mintTurnId`, sent on the stream body),
  Enter while `busy` posts the box as a note (`steerTurn`; the placeholder
  says so; a 404 keeps the words in the box), shows it under the live reply
  (`#steer-notes`), and folds the notes the hook collected off round frames
  (`ChatStreamResult.steered`) into the saved conversation as `user`
  messages flagged `steer: true` BEFORE the reply — the model saw them so,
  and the next turn resends the whole history. Stream lane only: `POST /chat`
  has no round boundary to consult. EDIT AND RESEND: `RowHandlers.editMessage(i)`
  cuts `messages` before a user bubble, saves the cut (`queueSave`) when
  anything precedes it, puts the text + files back in the box; never mid-turn
  and never on a steer note. Pins: `tests/test_chat_steer_v1278.py`,
  `dashboard/__tests__/chat-steer-v1278.test.tsx`,
  `dashboard/__tests__/chat-edit-message-v1278.test.tsx`.

- **A key opens the panel, Esc stops, Open Jarvis focuses, and the model menu
  is typed into; a gate proves concurrency by construction** (v1.277.0, /goal
  wave 4). Add-on: `manifest.commands["open-panel"]` (Alt+J) → the worker's
  `chrome.commands.onCommand` → `chrome.sidePanel.open({windowId})` (a command
  IS the user gesture that API needs); `background/openpage.ts::focusOrOpen`
  is the ONE opener — Open Jarvis matches `sitePattern(JARVIS_URL)` so any
  dashboard tab comes to the front, and the grant pages delegate to it;
  `keyToPress` answers `"stop"` for Escape while running (the v1264 pin
  moved). Chat: `lib/recentModels.ts` (`readRecentModels`/`rememberRecentModel`
  /`matchModels`) + the popover's `#model-filter` box — matches replace the
  tree while a word is typed, Enter picks the first, and `pickModel` is the
  one row handler (choice + setup + memory); the page reads storage only when
  the menu OPENS (S-05). GATES: `test_mcp_parallel_connect_v1257` asserted a
  wall-clock ratio and went red on the release runner at 3.21x with nothing
  wrong — it now proves overlap with a two-party `threading.Barrier` (serial
  code breaks the barrier; no clock is compared with any other). The
  batch-card test scopes its progress wait to the card (`within`) so a miss
  prints the card's DOM, and pins the row BEFORE the events. Pins:
  `tests/test_browser_sidebar_keys_v1277.py`,
  `dashboard/__tests__/model-menu-v1277.test.tsx`.

- **The risk door's card is answered where the turn is; a named click reads
  first; a loading page is read twice** (v1.276.0, /goal wave 3).
  `BrowserRuntime.approval_resolver` was None in production, so
  `_ActingTool._require_approval` filed a queue row and REFUSED ("approve it in
  Iron Jarvis, then make the identical call again") — a dead end from the
  sidebar for every floor case. Now `_consult_resolver` calls a resolver of
  any shape (sync one-arg as before; async with `(req, ctx, name=, args=)`),
  `ToolContext.turn_id` carries the panel's turn id from BOTH lanes
  (lock-step), and `PanelTurns.resolve_risk_ask` — wired by `panel.install`
  when nothing else resolves — answers ONLY its own turn: it files the ask in
  the one `ChatApprovals` registry, emits the panel's `approval` frame with
  the door's reason (`floor: true`), waits `RISK_ASK_TIMEOUT_S`, and the
  door's approve+consume keeps one Allow = one call. `read_page_snapshot`
  retries PAGE_NOT_READY once after `PAGE_NOT_READY_RETRY_S`;
  `prepare_action` auto-reads an unread tab for a role/name or css target
  (never for an element_id — ids come from a read). Pins:
  `tests/test_browser_capability_v1276.py`.

- **A send never goes without its files, and a file alone is a message**
  (v1.275.0, /goal wave 2). `page.tsx::send` used to return on empty text and
  ignored `uploading`, so Enter during an upload sent WITHOUT the files and
  nothing said so (the v1.244.0 "no output" shape by another door). Now:
  `uploadingRef` (the state lags a frame) queues the send (`queuedSendRef`)
  and `addFiles`' finally fires it with the box's CURRENT text; a send with
  attachments and no text goes (the lane accepts an empty user message —
  verified); `SendArrow` enables on attachments alone; `ComposerInput` has
  `onPasteFiles` (there was no paste handler at all — `clipboardData.files`
  → `addFilesRef`, plain text falls through); uploads run
  `UPLOAD_CONCURRENCY` (3) at a time with order kept. `lib/providerFallback.
  canRetryWithDefault` offers "Retry with the default model" ONLY when the
  explicit pick's provider is known-down and the default known-up — never a
  provider the user did not choose (v1.162.0). Pins: the workfolder suite
  (`chat-workfolder-v1244.test.tsx`) + `provider-fallback-v1275.test.ts`.

- **The same failing call is not run a third time; the ledger is the
  backlog** (v1.274.0). The daily driver's last 400 browser invocations were
  the evidence: a page read refused with the identical add-on error FIVE
  times in twenty seconds; a screenshot refused twice (background tab, then
  "Either the '<all_urls>' or 'activeTab' permission is required" on a grant
  covering the whole web). Fixes, each pinned: (1) BOTH chat lanes count
  failures per (tool, canonical args) and answer the third with
  `repeated_call_refusal` instead of running it — the stream lane BEFORE its
  card (`REPEATED_CALL_LIMIT`, lock-step); (2) `captureVisible` brings the
  tab on screen and captures; (3) `optional_host_permissions` is `<all_urls>`
  (Chromium's `CanCaptureVisiblePage` asks whether the granted hosts CONTAIN
  that pattern; two scheme patterns do not) — an older grant reads as "no site
  access" once and the sidebar now carries a Grant button
  (`request_host_permission`); (4) `PAGE_FAILED_TO_LOAD` (the eighteenth
  code) for "Frame with ID 0 is showing error page"; (5) the Edge store in
  `UNSUPPORTED_HOSTS`; (6) `open` sends the models frame from its own task
  (`_emit_models`) — it was awaited inline in the socket's read loop and a
  tab switch could stall every command; (7) `describe_browser_call` reads the
  NESTED `target` (the tools' real shape — the old pins used invented flat
  args) and never quotes the redaction marker; (8) `browser_agent_block`
  renders the roster from the armed set. Pins:
  `tests/test_repeated_call_breaker_v1274.py`,
  `tests/test_browser_ledger_fixes_v1274.py`,
  `tests/test_browser_sidebar_polish_v1274.py`.

- **The tab the agent is working in GLOWS; the worker knows from the commands
  it answers** (v1.273.0). "A light glow around the tab it is controlling so I
  can visually see the tab that is being operated by the agent."
  `background/glow.ts`: `Dispatcher.onResult` hands every SUCCESSFUL command
  result to `AgentGlow.afterCommand`; `glowTarget(method, result)` reads
  `tab_id` off it for the commands that work in a tab (`GLOW_METHODS`; not
  status/list_tabs/close_tab); `paintGlow(id)` runs INSIDE the page via
  `chrome.scripting.executeScript` — self-contained (the id is an argument),
  idempotent, `pointer-events:none`, max z-index, an inset box-shadow in the
  dashboard's accent `34,211,238`, reads nothing. Cleared on the sidebar's
  `done`/`error`/`state{running:false}` (`notePanelEvent`), on a socket that is
  not connected, on a closed tab, and by `GLOW_LINGER_MS` after the last
  command when no sidebar turn is known (the chat page's turns never reach the
  worker). A navigation unloads it; the navigate result repaints it. Pins:
  `tests/test_browser_agent_glow_v1273.py` (source + `glowTarget` under node).

- **A side panel cannot show the microphone prompt; a page in a tab can**
  (v1.272.0). The user: "when I try to use the mic option, it doesn't say I
  have permission without the ability to give it permission." Chromium answers
  `getUserMedia` from a side panel with NotAllowedError and NO prompt, and the
  v1.269.0 panel then told the user to allow it in "site permissions" — a
  place that does not exist for a side panel. Same shape as site access
  (setup.html): `src/mic/mic.html` + `mic.ts` is one button that calls
  `getUserMedia({audio: true})` INSIDE the click (no `await` before it — the
  gesture rule), stops the tracks at once, reports granted/refused/error and
  tells the worker (`mic_permission_result`); the panel's NotAllowedError
  branch posts `request_microphone`, the worker `openMicPage()`s (shared
  `openAddonPage`), then broadcasts `mic_permission` so the panel says
  "Microphone allowed". The permission belongs to the add-on's ORIGIN, so a
  grant on the page covers the panel. The page is in the bundler, the doctor's
  `BROWSER_ADDON_RUNTIME_FILES`, the theme test's PAGES and the copy-rule scan.
  Pins: `tests/test_browser_mic_page_v1272.py`, the runtime panel suite.

- **A navigation is judged by its DESTINATION, never by the page it leaves**
  (v1.271.1). From Edge's new-tab page: "UNSUPPORTED_PAGE: edge: pages are
  closed to add-ons by Chrome" — `prepare_action` → `resolve_page_tab` refused
  the ACTIVE tab for being `edge://`, so `browser_navigate` (no tab_id) failed
  and the model navigated a background tab instead; the job ran where the user
  was not looking. `resolve_page_tab(..., page_required=False)` resolves the
  tab (TAB_NOT_FOUND still) and skips the page check; navigate, activate_tab
  and close_tab pass it (via `prepare_action(page_required=False)`); read,
  click, type, press_key, scroll and screenshot keep it — a content script must
  run there. The destination check (`navigate_params`) is untouched. The
  refusal says "the browser", not Chrome. Pins:
  `tests/test_browser_newtab_navigate_v12711.py`.

- **The sidebar works in the tab the user is looking at; a new tab is a thing
  the user asks for** (v1.271.0). "It requires opening a new tab and doesn't
  just work in the tab I already have open. This is a real flaw." The model was
  TOLD to: `browser_navigate`'s description said "prefer browser_create_tab when
  the user should keep the page they are on", `browser_create_tab`'s said
  "Prefer this over browser_navigate", and `BROWSER_AGENT_BLOCK` never said
  where to work. Now both descriptions and the block say "the tab the user is
  looking at"; a PANEL turn's ceiling (`panel.py::_run`) drops
  `browser_create_tab` unless `wants_new_tab(text)` (`NEW_TAB_PATTERNS`,
  word-bounded) — the bound that holds when words do not; other surfaces keep
  the whole family. Same release: the transactional vocabulary reads a
  navigation's scheme+host+path (`risk.navigation_words`), never the query
  string — a Google search for "buy …" was refused as a transaction twice on
  the user's install while `/transfer` and `/checkout` still ask. Do not put
  "prefer a new tab" back into a description, and do not widen the vocabulary
  back over the query. Pins: `tests/test_browser_sidepanel_tab_v1271.py`.

- **A covered call must CARRY its grant into the registry; a test that stubs
  `invoke` cannot see that it did not** (v1.270.1). The user, switch on, on a
  live tab: "tab creation needs your approval in Settings". The ledger:
  `tool.denied browser_create_tab mode=ask` — "needs approval and nothing here
  could ask". `routes/chat.py` skipped the card for a call covered by a tab
  grant (v1.266.0) or the switch (v1.270.0) and left `_grant_extra` EMPTY, so
  `registry.invoke`'s own permission gate — which is what a card's `once`
  answer satisfies through `session_allow` — refused it. The tab grant shipped
  with this hole for two days because every case stubbed `registry.invoke`
  and asserted the stub was called with no `deny_reason`: intent, not outcome
  ("Arming is granting", again). Now the covered branch sets `_grant_extra`
  exactly as yolo does, and
  `tests/test_browser_sidepanel_context_v1270.py` drives the REAL `invoke`
  (only the tool's `execute` is stubbed) for the switch, the tab grant and a
  no-grant/Deny control. When a lane skips a human gate, prove the call RAN
  through the layer that would have refused it.

- **The sidebar's conversation lives in the daemon; its ONE switch is the
  per-tab grant widened to every tab; its steps fold** (v1.270.0). The user:
  "not keeping the items within the chat in context", "instead of permissions
  … a simple toggle for all permissions", "the specific detail of the process
  should go behind a thinking word … expandable". (1) `PanelTurns._history`
  (bounded by `PANEL_HISTORY_MESSAGES`) rides every turn's `ChatBody.messages`
  — the lane's own planner budgets it; `open` replays it (`PANEL_EVENT_HISTORY`)
  into an EMPTY transcript only, `reset` forgets it and is refused mid-turn.
  The daemon holds it because a side panel is destroyed on close. (2)
  `auto_allow {on}` → `TabGrants.grant_all` — `covers()` answers True for every
  tab (unknown tab included), so the lane's `_would_card`/`_needs_card` skip the
  card with NO lane change; turning it on resolves every card this panel has
  offered as `once`; it dies with a new `browser_session` and with Forget, and
  the panel re-asserts its `chrome.storage.local` setting on every `open`
  (`open {auto_allow: bool}` — a non-bool is ignored). The risk door
  (`_ActingTool._require_approval`) still never reads grants — the four floor
  cases still card, and the Handbook says so. The sidebar's card is Allow ·
  Always allow · Deny; the `tab`/`task` scopes remain for the chat page. (3)
  `tool` frames carry `status`/`ok`; the panel folds a step's start and end into
  one row under a collapsed `<details class="work">` — the route notice
  (`name == "route"`) stays a visible line. Pins:
  `tests/test_browser_sidepanel_context_v1270.py`, the runtime panel suite.

- **The sidebar's composer owns the model icon and a mic/send button;
  dictation rides the panel socket into the app's OWN voice engine**
  (v1.269.0). Model picker = the same `<select id="model">` (the D28 pin allows
  exactly one) sitting invisibly over a small icon in the composer's bottom
  row; its tooltip is where the pick shows. The button `#send` is decided from
  the BOX'S CONTENT at the press (`composerMode`): empty → microphone, words →
  arrow, listening → stop — never from a cached attribute, so a value set by
  code sends. Voice: `PANEL_ACTION_VOICE` `{op: start|chunk|stop|cancel,
  pcm_b64}` (16 kHz mono PCM16 from a ScriptProcessor + `floatTo16kPCM`) →
  `PanelTurns._voice*`: the bundled Vosk model streams partials
  (`PANEL_EVENT_TRANSCRIPT {text, partial, final}`), else the HTTP backend gets
  one WAV at stop through `routes.voice.transcribe_clip` — the route's body,
  LIFTED to module level with `voice_capability(d)` (what `/voice/status`
  answers; the `models` frame carries it as `voice` so the mic greys honestly).
  Never the browser vendor's speech service: the audio is the user's. Panel
  vocabulary is now 8 actions / 9 events (protocol pin updated). Header: the
  arc-reactor mark as inline SVG with the connection dot on it; no select up
  there. Pins: `tests/test_browser_sidepanel_voice_v1269.py`, the runtime panel
  suite.

- **The add-on's worker is kept alive by the DAEMON's heartbeat; a timer in
  the worker cannot do it** (v1.268.0). "It randomly disconnected and
  reconnected": Chromium evicts an MV3 service worker after ~30 s without
  events and the socket dies with it; the next tab switch woke it and it paired
  back in. WebSocket traffic inside the window resets the timer (Chrome 116+),
  so `routes/browser.py::_keepalive` sends `browser.ping` every `KEEPALIVE_S`
  (protocol.py, 20 s) on every idle tick of a PAIRED socket — adopted or inert,
  never restricted — and `socket.ts` answers `browser.pong` (`pongFor`, pinned
  under node); the backend stamps `conn.last_pong_at` and `GET /browser/status`
  reports `keepalive`. Do not "simplify" the interval past 30 s, and do not add
  `alarms` to the manifest for this (D26). Pins:
  `tests/test_browser_keepalive_v1268.py`. Same release: `sidepanel.html` wears
  the app's look — dark `--cyan` IS the dashboard's `--accent-rgb`, light
  `--cyan` its light accent family; bubbles, a rounded composer, no hard
  dividers, pill buttons — `tests/test_browser_sidepanel_look_v1268.py` pins
  the structure and the cross-file accent, and the theme test still owns the
  contrast.

- **The sidebar prints only what changes what the user can do, and it has a
  model picker — the user's decision, reversing D28's "no model picker"**
  (v1.267.0). "Not too much in the way of instruction when the extension is
  opened. Super clean and minimal. I should also be able to select the model."
  `sidepanel.html`: no `<h1>`, no `#mode-hint`/`#running-hint`/`#keys-hint`
  paragraphs — every explanation is a `title=` tooltip (the v1242/v1264 pins
  read the HTML SOURCE, so they still find the words); the access mode is a
  pill; the add-on version + Open Jarvis sit in the footer meta line;
  `tests/test_browser_sidepanel_minimal_v1267.py` counts the idle panel's
  visible words and fails when instruction creeps back. THE MODEL: `open`
  also emits `PANEL_EVENT_MODELS` — `PanelTurns.models()` runs
  `routes.connections.selectable_models` (THE catalog behind `GET /models`)
  off the loop, projected to `MODEL_ROW_KEYS`, cached `models_ttl_s` (open is
  posted on every tab switch); the pick is stored in the add-on
  (`chrome.storage.local` `ij.panel.model`) and rides every `send` as
  `provider`/`model`; `_refuse_pick` refuses an unknown or unavailable pick with
  one sentence BEFORE a turn starts; the pick rides `ChatBody.provider/model`
  so routing, the v1.162.0 refusal and the route disclosure are the chat
  page's; `_route_notice` prints one muted line only for `failover`/`mock`
  (the receipt's amber cases). Do not add a second catalog in the add-on.

- **`asyncio.sleep(d)` measures LESS than `d` on Windows; bill by an injected
  clock, never by a sleep** (v1.266.1). `test_goals_trust_v1231.py` slept 50 ms
  inside a goal run and asserted the engine billed `>= 0.05` s of real wall
  clock; the v1.266.0 Tests gate went red on `assert 0.047 >= 0.05` with no
  goals code changed. Windows' monotonic clock ticks at 15.6 ms and asyncio
  fires a timer up to one clock resolution EARLY, so the sleep is the runner's
  fact, not the engine's. The engine reads `time.monotonic()` through its
  module's `time`; the test now monkeypatches that name with a stand-in the
  fake run advances by 0.05 and asserts the bill EXACTLY. Same family as the
  p95 rule: a threshold on a sleep is a threshold on the hardware.

- **One approval per tab: the grant lives on the runtime, the lane consults
  it, the risk gate never does** (v1.266.0). The user: "I need to keep on
  providing approvals over and over." Every page action in the sidebar is
  ask-armed (v1.262.0) and `conversation` covers one turn, so each Send
  re-asked. `browser/grants.py` (`TabGrants`, owned by
  `BrowserRuntime.tab_grants`) holds granted tab ids; the stream lane's
  `_would_card`/`_needs_card` skip the card when
  `runtime.tab_grant_covers(tool, args)` (an ACTING browser tool whose
  effective tab — `tab_id` arg, else the cached active tab — is granted); the
  decision word `tab` (DECISIONS; the panel's `scope: "tab"`; the chat card's
  "Allow for this tab") grants the tab BEFORE the `approval_resolved` frame and
  is then treated as `once` (the agent runtime reads `tab` as `once`). A grant
  ends on the add-on's `tab_removed` event (`chrome.tabs.onRemoved`), on a new
  `browser_session` in `browser.hello` (minted in `chrome.storage.session` —
  survives worker restarts, dies with the browser; tab ids are per session), and
  on Forget — NEVER on a socket reconnect, which happens every ~30 s idle and
  would put the cards back. `_ActingTool.execute` / `risk.py` do not read
  grants: a payment, a password field, the destructive vocabulary and a flagged
  page still card. Pins: `tests/test_browser_tab_grant_v1266.py`,
  `dashboard/__tests__/tab-grant-v1266.test.tsx`, the runtime panel test.

- **Mint AFTER the check, and undo a mint that was not delivered** (v1.265.0).
  `BrowserRuntime.complete_pairing` minted the pairing row FIRST and looked for
  the requesting socket SECOND; when the add-on's socket had closed (a
  service-worker restart — it reconnects three seconds later) the row stayed
  unrevoked with `last_seen_at` NULL, and `PairingStore.mint`'s already-paired
  guard then refused every later press with "Press Forget…" while the card
  rendered Forget only under Connected. Three fixes: the socket route's teardown
  drops an unpaired socket's request from the store (`_drop_offer`); the service
  checks pending + open socket before minting and calls `revoke_token` if
  delivery fails; a Pair press passes `allow_replace=True` (old live rows are
  revoked in the same transaction as the insert) and is refused ONLY when
  another browser is paired AND `backend.connected`. The gone-socket test had
  asserted `backend.connected is False` and nothing about the store — intent,
  not outcome. Pins: `tests/test_browser_pairing_orphan_v1265.py`,
  `dashboard/__tests__/browser-pairing-v1265.test.tsx`. Also: the add-on's
  `sidepanel.html`/`setup.html` are two-palette (light default, dark under
  `prefers-color-scheme`), every colour a `:root` token —
  `tests/test_browser_addon_theme_v1265.py` scans for literals outside the
  palettes and checks WCAG AA in both themes.

- **A browser tab has no token, and "restart" is not the answer to a 401**
  (v1.264.0). The add-on's Open Jarvis opens the dashboard in the BROWSER; the
  page then 401s everywhere, and the Browser card read its own 401 as "no
  Browser surface — restart Iron Jarvis". Now: `desktop/main.js` owns
  `ironjarvis://` (`setAsDefaultProtocolClient`; packaged AND dev forms), a
  SEPARATE `second-instance` listener registered AFTER the plain one (that one
  is lifted verbatim by `test_desktop_lifecycle_v1192` — the FIRST such block —
  so it must not change shape, and `createMainWindow`'s `loadURL(DASHBOARD_URL)`
  tail is pinned by `test_desktop_reliability_v1226`, so the pending path loads
  AFTER it) plus `open-url` and the cold-start argv all funnel through
  `dashboardPathFromProtocolUrl` (dashboard path only — no host, no dots, no
  `..`, no `//`) into `openDashboardPath`, which shows the window at that page
  or parks it in `pendingProtocolPath` for the first window. The token banner
  outside the desktop shell links `ironjarvis://<path>` (`appLink`, same rule)
  and names `token.txt`; `YourBrowserCard` tells 401/403 ("not signed in")
  from 404 ("older daemon"). Add-on: Enter sends / Steer while running /
  Shift+Enter newline (`keyToPress`, reading the ONE running flag
  `document.body.dataset.turn`); `toggleAction("offline")` is `connect`
  (`resume` resets the backoff and reconnects now); the offline words name the
  cause. `tests/test_sidebar_first_minute_v1264.py`,
  `dashboard/__tests__/sidebar-first-minute-v1264.test.tsx`.

- **The gate collects what CI collects, runs on CI's Python, and a hang fails
  BY NAME** (v1.286.0, deep review wave 1). The local full suite hung at 97%
  twice on 2026-09-21 while CI passed the same commit: pytest recursed into
  UNTRACKED review scratch dirs (`tests/_audit_*`, `tests/_review_*`) whose
  stale approval tests wait forever. `norecursedirs` in pyproject now names
  them — and because setting it REPLACES pytest's defaults, the defaults are
  repeated there; put review scratch tests ONLY under those prefixes (named
  explicitly they still run). `pytest-timeout` sets `timeout = 300` per test:
  under xdist a hung test fails as "worker 'gwN' crashed while running
  '<node id>'" and the run finishes; a test that honestly needs longer takes
  `@pytest.mark.timeout(N)`, never a higher global. Every CI job carries
  `timeout-minutes` (~2x its measured runtime). `.python-version` pins the dev
  venv to 3.12 — CI's and the frozen app's interpreter; on 3.12 Windows
  `utcnow()` ticks every 15.6 ms, so `created_at` TIES ARE ROUTINE: never break
  one with a random `new_id()` (the blackboard did, and flipped order on CI) —
  tie-break on the SQLite `rowid` (insertion order). Liveness tests assert the
  offloaded work ran OFF the loop thread (thread identity / heartbeat reaching
  a target), never a max-gap bar: a 1.83 s unrelated stall on CI failed correct
  code. `tests/test_suite_collection_v1286.py`,
  `tests/test_gate_timeouts_v1286.py`, `tests/test_blackboard_order_v1286.py`.

- **Stop is a KILL, a steer is never dropped, a tool round is budgeted**
  (v1.287.0, deep review wave 2). (1) A CLI provider (`subprocess_cli`,
  `opencode_cli`) runs as Popen in a worker thread with
  `communicate(timeout)`; on CancelledError or TimeoutExpired the TREE is
  killed (`sandbox/native._kill_tree`) and the error re-raised —
  `to_thread(subprocess.run(timeout=))` is neither cancellable nor bounded
  when a `.cmd` shim or helper holds the pipe. The injected
  `runner(argv, stdin)` seam is for test doubles only. (2) A named turn races
  every router frame against `TurnHandle.wait_stopped()`
  (`_frames_until_stop`; `stop()` wakes it via `call_soon_threadsafe`), so a
  Stop before the first word lands now. ONE pump task drives the router
  stream (task-affine timeouts/cancel scopes held across a yield keep
  working), and a pump that dies without its end marker fails the turn
  instead of parking it. (3) A steer note that reaches no round boundary is
  handed back as `done.unread_steers` (`TurnHandle.close_steers` → later notes
  404), read from the REGISTRY queue only; the page puts it back in the
  composer. (4) Every completion in both chat lanes sends
  `chat_turn._fit_turn_transcript(...)` (reuses `plan_agent_transcript`, the
  question protected, acts only when the window is known and overflowed) —
  never pass raw `msgs` to a completion again. (5) A transport death mid-answer
  gets the "incomplete, retry" refusal for ANY provider (wording only, no
  failover; cloud advice names the connection, not an endpoint), and both
  lanes build error text with `chat_turn._error_detail`, never an empty
  `str(exc)`. Pins: `tests/test_cli_cancel_kill_v1287.py`,
  `test_chat_stop_first_token_v1287.py`, `test_chat_steer_unread_v1287.py`,
  `test_chat_inturn_budget_v1287.py`, `test_chat_error_detail_v1287.py`,
  `dashboard/__tests__/chat-steer-unread-v1287.test.tsx`.

- **Background work you can trust** (v1.292.0, deep review wave 5).
  (platform-01) A SYNC event-bus handler never sees a running loop in
  production — `EventBus._dispatch` runs it via `to_thread` — so
  `except RuntimeError: pass` around `get_running_loop().create_task(...)`
  drops the work EVERY time, not just in unit tests (a scheduled living-doc
  refresh said "done" and regenerated nothing). The shape is
  `_publish_skill_proposal`'s: create the coroutine once; on RuntimeError hop
  onto `_live_rearm["loop"]` with `run_coroutine_threadsafe`, else
  `coro.close()` + WARN. Grep for that `pass` before adding a handler.
  (platform-04) A background pump armed once at boot must be a LIVE re-arm:
  `_live_rearm["slack"]` = `_arm_slack_socket` (one holder for the CURRENT
  stop Event + task; sets the old stop, awaits the old task, re-probes
  `enabled()`, starts a fresh pump whose token is read per run), called from
  POST/DELETE `/comm/channels` when the type is slack via
  `_rearm_slack_socket` (hops onto the loop with `call_soon_threadsafe`, as
  the calendar re-arm does); shutdown sets whichever stop is current, and a
  re-arm landing after the finally never starts a pump. (platform-06) An
  outbound webhook with no event types is REFUSED (400, plain words) — an
  empty list matched nothing while the page said "all"; `DELETE
  /webhooks/{slug}` pops the in-memory inbound handler AND the outbound
  `_secrets` cache entry but leaves the vault secret (`secret_name` may be
  shared); the page has a two-press Remove. (platform-02) `create_backup`
  excludes `<home>/trash` (resolved) next to the media dirs — `clear_media`
  moves media there, and every later backup + mirror archived it.
  (platform-08) `storage_report` lists workspaces, uploads, remote-inbox,
  livedocs and documents (`clearable=False`) plus an "Everything else" row =
  the whole walk minus the listed folders, so `total_bytes` is the disk;
  `clear_media`'s scope (`_MEDIA_DIRS`) is unchanged and pinned. Also:
  `projects/knowledge.list_knowledge` orders by `created_at DESC, rowid
  DESC` — the same-tick tie (v1.286.0 blackboard lesson) made the gate's
  `test_add_list_remove` fail two runs in three here. v1.292.1 (test-only):
  the release gate went red twice on a slow shared runner (28-31 min suites)
  on two timing tests -- the ConPTY kill wait is 90 s (liveness, matching the
  EOF wait) and the search-index fairness floor is 500 ms (the pathology was
  seconds). Two tasks
  edited `routes/comm.py` concurrently: targeted Edits only — a whole-file
  restore during a mutation check re-landed a line into the other task's
  region once. Pins: `tests/test_livedoc_schedule_v1292.py`,
  `test_slack_socket_rearm_v1292.py`, `test_webhooks_manage_v1292.py` (+
  `dashboard/__tests__/webhooks-manage-v1292.test.tsx`),
  `test_maintenance_storage_v1292.py`.

- **Packs, spreadsheets and the phone** (v1.291.0, deep review wave 4).
  (io-01) A read_only openpyxl sheet RE-PARSES ITS XML FROM ROW 1 on every
  `iter_rows(min_row=r, max_row=r)`, so a per-row lookup is O(rows²) (1,000
  rows with one blank column: 25 s). `_read_xlsx` walks the value and formula
  passes in LOCKSTEP by POSITION (`enumerate` + `next(frows)`), opening the
  formula pass lazily on the first `None` cell; never `.row`/`.coordinate` —
  an unwritten blank arrives as `EmptyCell`, which has neither (that was a
  crash, not just a slowdown). The pin is a RATIO (blank column vs none).
  (io-02) `MCPClient._request` runs a synchronous transport OFF THE LOOP
  (`asyncio.to_thread`) under a per-transport lock (ids and the single stdout
  reader never interleave); `StdioTransport` reads on a reader thread + queue
  with a deadline; on expiry or cancel `abort()` KILLS the child (a parked
  readline must never keep owning the pipe) so the next call respawns. The
  registry deadline (`config.tool_call_timeout_s`) is the ONE bound on a
  pack call: a registry transport takes `request_timeout=None`; only
  deadline-less callers (the LTM brain, driven through `asyncio.run`) get
  `DEFAULT_REQUEST_TIMEOUT_S`. A transport floor shorter than the caller's
  deadline is a bug. (io-05) The child is spawned with `encoding="utf-8",
  errors="replace"` (MCP stdio is UTF-8 by spec; cp1252 raised on C3 8D) and
  `stdio_shim` wraps `sys.stdin/stdout.buffer` in UTF-8 TextIOWrappers.
  (io-06) `_ensure_started` respawns when `_proc.poll()` is not None; EOF /
  OSError / BrokenPipe mark the process dead and raise an `MCPError` that
  NAMES the pack and says it restarts on the next call — never auto-retry
  (a tools/call may have had side effects). `close()` kills the process TREE
  (kill-on-close Job on Windows — npx runs cmd.exe with node as a grandchild —
  then the pipes, so a parked readline returns). (io-03) The phone poll
  never awaits a session: `_handle` creates it, acks inline, and runs it in a
  tracked task (`_spawn_delivery`; set + done-callback; cancelled at
  lifespan shutdown) that sends the summary and `_safe_append`s it; the
  inflight marker covers DISPATCH only. Two restart paths tell the phone: a
  crash → boot reconcile stamps `interrupted_at` → `notify_interrupted(since=
  boot)` (sent to the single allowed sender's chat when the channel has no
  `chat_id`); a graceful shutdown → `cancel_background()` sets
  `_shutting_down` and the cancelled delivery RE-ARMS the inflight marker so
  the next boot's `_recover_inflight` sends `DROPPED_REPLY` — a desktop
  Cancel does not re-arm. A pin that awaits `_handle` then reads the outcome
  needs `await poller.drain()`. (io-04) The frozen exe's entry is the Typer
  CLI, so `-m iron_jarvis.mcpserver.stdio_shim` died with "No such option":
  hidden `ironjarvis mcp-stdio` (lazy import, nothing on stdout but the
  protocol); `CodexRecipe.config_text` writes `["mcp-stdio"]` under
  `sys.frozen`, else `["-m", module]`, and `_shim_available` agrees.
  RELEASE CHECKLIST: after an installer builds, run
  `"<install>\resources\daemon\ironjarvis.exe" mcp-stdio` with
  IRONJARVIS_MCP_URL/TOKEN set and feed one `initialize` line — one JSON-RPC
  line must come back (exit 0). Pins: `tests/test_xlsx_blank_cells_v1291.py`,
  `test_mcp_stdio_transport_v1291.py` (+ `tests/fixtures/stdio_mcp_server_v1291.py`),
  `test_phone_job_background_v1291.py`, `test_codex_frozen_shim_v1291.py`.

- **Safety checks, other agents' history, and a read-only Memory door**
  (v1.290.0, from agent-beacon, MIT). THREE parts, one normalized EVENT dict
  (`action`, `session_id`, `source`, `ts`, `tool`, `command`, `path`, `url`,
  `text`, `ok`, `ref`, optional `subagent`).
  (1) `iron_jarvis/detections/`: YAML rules (`rules/*.rule.yaml`, Beacon's
  shape with a DECLARATIVE matcher instead of CEL — no new dependency;
  `correlation:` for ordered multi-step rules in one session within a window).
  `load_rules` REFUSES a rule without both a match and a no_match fixture and
  the pin runs every fixture. Commands are judged ONE LINE at a time, every
  segment class stops at `\n`, command/path/url are capped (ReDoS: an
  unbounded `.*` rule took 12.8 s on 4 KB; a regex opening `\b(word|…)` only
  runs when a word is present). A heredoc that only WRITES a file is content,
  not a command; one fed to an interpreter is code and is scanned. Correlation
  is linear (per-step match index + bisect); both perf pins are RATIOS.
  `ledger.py` maps ToolInvocation rows (denial = a persisted `tool.denied`
  event, which is authoritative — an ask refusal keeps verdict `ask`). CHAT has
  no per-turn id in the ledger (`session_id`/`agent_run_id` = "chat"): rows are
  grouped by the turn's AgentRun window (`_persist_chat_usage`) into
  `chat:<run id>`, and the post-turn scan hooks there (both lanes). `bell.py`
  publishes `detection.finding` for high/critical ONLY, once per (rule,
  session) per process, dedup marked AFTER a successful publish. THE BELL
  PAYLOAD CARRIES NO COMMAND, URL OR OUTPUT (`BELL_EVIDENCE_KEYS`: action,
  tool, ref, masked path, ts, source): the event lands in EventRecord, the
  WebSocket AND the daemon log (platform.py logs payloads), and the rule most
  likely to fire is the one that selects commands carrying tokens.
  `redact.mask` masks secrets (Authorization values, key=value secrets,
  `-u user:pass`, `user:pass@` URLs, ghp_/sk-/sk-ant-/xox/AKIA shapes) in
  every `Finding.to_dict`. Scans are bounded: 2 running + 32 queued, the rest
  dropped and counted. Routes `/detections/rules`, `/detections/findings`. Tuning rule: every false positive found on real
  history becomes a generic no_match fixture BEFORE the regex changes.
  (2) `iron_jarvis/history/`: READ-ONLY reader of `~/.claude/projects` (or
  CLAUDE_CONFIG_DIR) and `~/.codex/sessions` (or CODEX_HOME) — `open(…, "rb")`
  only, in-memory cache keyed by (path, mtime_ns, size, subagent signature),
  evicted when the file goes. Claude SUBAGENT transcripts
  (`<uuid>/subagents/**`) are FOLDED into their session (tagged `subagent`) —
  they hold ~85% of the tool calls on this PC, so leaving them out made "no
  findings" a false claim. Cap is by BYTES read (50 MB), `truncated` from what
  was really skipped. The session id is MATCHED against the listing, never
  joined into a path. Routes `/history/sessions[/{harness}/{id}]`; the detail
  is PAGED (`offset`/`limit` ≤ 2000, default 500, `events_total`) — a heavy
  session is 20k events / 20-39 MB — with `findings` over ALL events cached
  per (session, file signature), and at most 2 session loads at once. The
  listing stats first and summarises only the newest `limit` files; a cold
  full listing byte-scans the subagent logs (~3 s, off the loop). Anyone with
  the install token can read these transcripts (said in the Handbook).
  (3) `mcpserver/server.py` `CAPABILITY_TOOLS`: a capability is a prefix OR an
  EXACT name set; `memory` = {memory_search, memory_read, ltm_search} — never a
  `memory_` prefix (that would arm memory_write/memory_propose). Enforced on the
  Build-pane MCP door ONLY — the pane's chat is not gated, which is why
  `terminals/session.ENFORCED_PANE_CAPABILITIES` stays ("browser",).
  `ltm/mcp_brain._pick_search` refuses write-like tool names (whole words) and
  tools without a string query parameter, and calls nothing when no safe
  search tool exists. Dashboard: `components/SafetyChecks.tsx` (Activity
  `#safety`; a chat finding links to /chat), `AgentHistory.tsx`, the bell's
  `toActivity` maps `detection.finding`. Pins:
  `tests/test_detections_v1290.py`, `test_history_v1290.py`,
  `test_mcp_memory_capability_v1290.py`, `test_mcp_brain_search_pick_v1290.py`,
  `dashboard/__tests__/safety-and-history-v1290.test.tsx`.

- **An Overview tile never leaves the screen; pushing it off opens the
  module in its own window** (v1.289.0). Sortable's transform followed the
  pointer without limit, so a tile could be dragged clean off the window
  (the auto-scroller chasing it) and the Overview was wrecked until a
  reload. `lib/tileDrag.ts` is pure: `clampToWindow` (the dnd-kit modifier
  on `AppGrid`'s DndContext keeps the dragged tile's rect inside the
  viewport) and `offscreenEdge` (more than half the tile past an edge, read
  from the RAW translate — the modifier records it in a ref because
  dnd-kit's drag-move `delta` is post-modifier and would hide the intent).
  While armed the tile rings (`data-armed`) and a hint says what the drop
  does (`#tile-edge-hint`, `data-edge`). On release past an edge: the
  v1.283.0 pop-out (`popoutBridge().open(href)`) in the desktop app, the
  arrangement untouched (it was not a rearrange); in a browser nothing to
  open, the tile stays where the clamp held it. `@dnd-kit/modifiers` is
  NOT a dependency — the clamp is nine lines. Pin:
  `dashboard/__tests__/tile-offscreen-popout-v1289.test.tsx` (the gesture
  driven through the real grid with PointerSensor on jsdom).

- **The Overview desktop is three screens of ten — Office, Operations,
  System — and only the current screen is in the DOM** (v1.293.0).
  `lib/appTiles.ts::TILE_GROUPS` places every tile (by href) in exactly one
  group with an icon and a one-line hint; `slides(orderedTiles(...))`
  partitions the flat order WITHOUT re-sorting (arrangement → most-used →
  catalogue still decide inside a screen); an unplaced page lands on a
  trailing "More" screen and the pin fails until it is placed; `SLIDE_SIZE`
  (10) is asserted per group. A rearrangement is still ONE saved list:
  `reorderWithinSlide(flat, slideIds, from, to)` re-sequences the screen's
  tiles in the slots they already hold, so `ironjarvis.overview.order`, its
  shape and `orderedTiles` are untouched and a pre-v1.293.0 arrangement
  renders the same relative order. NOT a scrolling track: an overflow
  container clips a tile transformed outside it, and the v1.289.0 gesture
  drags to the WINDOW edge — so `AppGrid` renders one grid (keyed on the
  group, `m.div` slides it in) inside one `SortableContext` of the ten
  visible ids, and the clamp/edge-hint/pop-out code is byte-for-byte the
  v1.289.0 code. Screens change by tab (`role="tab"`, `#tile-group-<key>`),
  chevrons (`#slides-prev/next`), arrow keys ONLY when the strip itself is
  the target (a focused tile keeps its keys for dnd-kit's keyboard sort),
  horizontal wheel (one screen per gesture: `WHEEL_COOLDOWN_MS`) and a
  pointer pull judged on the DOCUMENT's pointerup — a tile drag that
  started sets `swipeBlocked` (read at release; the dragging STATE is
  already false by then because dnd-kit's drag-end runs first) so a drop
  never flips the screen. The screen is remembered by group KEY
  (`ironjarvis.overview.slide`), never by index. Pin:
  `dashboard/__tests__/overview-slides-v1293.test.tsx` (six mutations each
  go red: block removed, slot-preserving reorder replaced, eleven on a
  screen, wheel cooldown removed, strip keys stealing from a tile, slide
  not remembered).
  **SUPERSEDED IN v1.294.0 at the user's request ("no more need for
  sliding"): the desktop is now THREE DOORS.** `AppGrid` renders three large
  `GroupDoor` buttons (`#group-<key>`, icon + label + hint + count); a press
  sets `openKey` and the other two are gone from the DOM while that group's
  ten tiles render in place under a back control (`#group-back`) — Esc on
  the grid (`#module-grid`) closes too, never mid-drag. The open group is
  remembered by key in `ironjarvis.overview.group` (`readOpenGroup` /
  `writeOpenGroup(null)` = closed); the slide key, the tab strip, chevrons,
  swipe, wheel and arrow handlers are all gone. Helpers renamed:
  `groupedTiles` (was `slides`), `reorderWithinGroup`, `GROUP_SIZE`,
  `TileGroup`. Pin: `dashboard/__tests__/overview-groups-v1294.test.tsx`;
  the v1.289.0 gesture pin opens Office first (`renderOpen`).

- **A custom agent is an EMPLOYEE: a job card, a monthly allowance, a day
  off** (v1.295.0, /goal wave 1 of the agents borrow list). `DynamicAgentRecord`
  gained additive columns (approval_mode, max_steps, allowance_tokens,
  allowance_usd, allowance_warned_month, paused_reason/paused_at, reports_to,
  skills_json, deny_tools_json); pre-wave rows hold NULL on disk and ONLY the
  registry normalises them (`dynamic._with_defaults`) — read the table through
  `agents_registry`, never raw. `register()` keeps its positional contract;
  the new kwargs default to `_KEEP` (omitted = stored value kept), so PATCH
  one field never clobbers the rest; an explicit `max_steps=None` clears.
  `agents/allowance.py` is the ledger: `month_spend` sums `Session` rows
  credited `custom:<slug>` (the spawn/delegate tools now STAMP child tokens
  onto the child row — before this wave they carried 0) and prices them with
  `eval.pricing.cost_for`; `allowance_state` → ok / warning (≥80%) /
  exhausted (≥100%) / unlimited, pct over the SET bounds only;
  `refusal_for(record, engine)` is ONE plain sentence or ""; `after_run` is
  ASYNC (it awaits the bus) and is awaited by `Orchestrator._post_run_allowance`
  right after every `_post_run_learning` call — it pauses once
  (`agent.paused`) and warns once per month (`agent.allowance_warning`,
  dedupe stored on the row). EVERY DOOR REFUSES HONESTLY, never a silent
  builder: `POST /agents/{name}/spawn` 409 (refusal_for via to_thread), the
  `spawn_agent` and `delegate` tools (incl. the bare-slug fallback, which used
  to fall through `AgentType()` into a builder run), and the schedule fire
  (RuntimeError, like its supervisor refusal), the PHONE escalation
  (`comm/inbound._escalate_plan` replies the sentence — it used to fall to
  the supervisor silently, and `_run_dynamic_session` now stamps tokens and
  awaits the allowance tail), the round-table seat and `consult` (both answer
  the pause sentence, no model call). A PATCH that raises the allowance of an
  agent AUTO-paused for it resumes it and clears the warned month; a user
  pause never auto-resumes. The record's approval_mode and
  max_steps ride every door when the caller gave none. `deny_tools` →
  `AgentDefinition.permission_overrides={t: "deny"}` (deny-floor: narrows
  only); `skills` are injected like `default_skills`; `reports_to` adds one
  manager sentence. `providers/budget.py` holds a ContextVar the runtime arms
  with the agent's remaining dollars for the run, and ONLY `ClaudeCliAdapter`
  reads it (`--max-budget-usd`, ≥ 0.05, the CLI's floor). Roster entries
  carry paused/pause_reason/allowance/reports_to and a paused agent is
  `healthy=False`. Routes: `POST /agents` 409s on an existing name (the
  `create_agent` TOOL still upserts — a model-driven door, by design);
  `POST /agents/{name}/pause|resume`; every employee 422 is one sentence.
  Dashboard: `SetupCard` "Employee details" + "Job card", `NameChips`
  (skills/deny), `AllowanceMeter` (`data-status` = the daemon's word),
  `PausedPill`; `lib/format.ts::formatTokens/allowanceSummary` are shared by
  the rail AND the bell (the bell must not import RosterStrip → framer).
  Pins: `tests/test_agent_allowance_v1295.py`,
  `tests/test_agent_employee_routes_v1295.py`,
  `dashboard/__tests__/agent-employee-v1295.test.tsx`.

- **An ASSIGNMENT is a durable job queued for a named agent; the dispatcher
  runs it when the agent is free** (v1.296.0, /goal wave 2). There is NO
  task entity in this app — the project Board is Session rows — so
  `src/iron_jarvis/assignments/` is the one queue: `AssignmentRecord`
  (assignee roster name, task/title, priority, status
  queued|claimed|running|done|failed|blocked|cancelled as plain str, source
  user|agent:<sid>|project|api|retry:<id>, reason, payload {allow_tools,
  workspace_root, max_steps, provider, model}, idempotency_key +
  coalesced_count, attempts/failure_count, blocked_reason, held_reason,
  depth ≤ 2, claim_token, session_id). `store.resolve_assignee` refuses
  unknown, blank, remote:*, supervisor and ANY definition carrying
  `delegate` (planner, custom coordinators) — the dashboard filters
  {supervisor, planner}; keep the rule keyed on `delegate`, do not widen.
  `claim_next` is a compare-and-swap (UPDATE … WHERE status='queued' AND
  id=<pick>, token read back; ≤5 re-picks). `finish` blocks at 3 failures
  (retry of a failed row CARRIES the count; failed is terminal, nothing
  auto-retries). The dispatcher (`AssignmentDispatcher`, daemon lifespan
  `bg_tasks["dispatcher"]`, `_tick("dispatcher")`, 5 s + `wake()`) holds
  with a plain sentence when `allowance.refusal_for` says so, when the agent
  already has a claimed/running assignment (one at a time), or when the
  global governor is full; it starts via `create_session(origin=
  "assignment:<id>", agent_name=…, approval_mode/max_steps from the record)`
  + `spawn_managed(run_session(definition=))`. "assignment" is in
  ASKING_ORIGINS (a card can ask) and NOT in ATTENDED_ORIGINS (unattended
  clock). `Orchestrator._post_run_assignment` settles the row on every
  terminal path (run_session, _finalize_failed, _finalize_cancelled — a
  CANCELLED session ends the row `cancelled` with NO strike, in either
  ordering with the route's own cancel; the reviewer found both doers had
  pinned `failed`+1) and publishes assignment.finished; `tick()` is
  serialized by an asyncio.Lock (two overlapping ticks double-started one
  agent); `build_roster(platform, with_health=False)` for every PROMPT-side
  caller (roster_block, delegable_names, resolve_target, consult) — the
  health fold is four SQLite reads per entry and belongs off the loop (the
  roster route, the inbox); boot runs `requeue_lost_at_boot` AFTER the
  interrupted-session reconcile — an interrupted assignment goes back to
  queued with held_reason "restarted after an update or crash" and NO
  strike, one assignment.requeued per boot. Events: assignment.created /
  started / finished / blocked / requeued. The store publishes from worker
  threads by hopping to its bound loop (`bind_loop`); `flush_events()` is
  for tests/routes. `assign_work` tool: ask tier, SAFE_HEADLESS like
  delegate, depth = caller's +1, NOT on any builtin roster (supervisor and
  planner already delegate) — custom agents opt in by name. Roster:
  `RosterEntry.health` (`assignments.health.agent_health`: last_run_at /
  last_outcome / last_error / last_wake_at / queued / running / blocked) and
  `activity` now says "idle" only when liveness is readable and nothing is
  queued or running (`_liveness_readable`). Routes: `routes/assignments.py`
  (POST 201/200-coalesced, GET list/get, cancel — a RUNNING one cancels the
  session first —, unblock, retry 201; 409 by STATUS, decided in the route;
  503 without the store), `GET /agents/{name}/inbox` (roster or bare name),
  `POST /projects/{id}/task` with `assignee` → 202 {assignment, queued}.
  Dashboard: `AgentInbox` (in `AgentsModal` AgentDetail; 404 = older daemon
  → renders nothing), `AssignmentRow` shared with `ProjectTasks`, JobPostCard
  "Queue it" (not for Team/remote/coordinators) + `onOpenAgent` wired on
  app/agents/page.tsx (the modal gate is `agentsOpen && hasRoster` now, not
  `&& room`), RosterStrip health caption + idle pill, bell maps blocked /
  failed / requeued (success is quiet). Pins:
  `tests/test_assignments_v1296.py`, `tests/test_assignments_routes_v1296.py`,
  `dashboard/__tests__/agent-inbox-v1296.test.tsx`.

- **An agent keeps a FOLDER, learns from a COACH, and its skills are
  CURATED** (v1.297.0, /goal wave 3). FOLDER: `agents/files.py`
  `AgentFiles(home)` over `<home>/agents/<slug>/` (slug = `agent_slug`, the
  avatar rule; every path confined): `AGENTS.md` mirrors
  `DynamicAgentRecord.system_prompt` (the DB stays what the runtime reads;
  the registry's `register(..., prompt_reason=)` writes the file and a
  `revisions/<stamp>-<reason>.md` of the PREVIOUS text, ≤ 50, restore leaves
  a revision too; `load()` backfills a missing file once), `NOTES.md` is the
  agent's notebook (≤ 16 KiB; injected after its own skills, head+tail
  trimmed to 4,000 chars; the `notebook` tool — allow tier, ARMED AT THE RUNTIME SEAM for every
  custom run after `arm_for_task` (NOT on the roster/`effective_tools`:
  test_carried_defects_v1185 pins the tool count), refuses a builtin —
  appends `- [date] text`);
  `remove()` moves the folder to `<home>/trash/` (never deletes). Routes
  `GET/PUT /agents/{name}/files[/instructions|/notes]`, revisions view +
  restore, `/files/open`. COACH: `coach/` — `signals.run_signals` reads ONLY
  the ledger (session_result, ToolInvocation, tool.denied events,
  OutcomeRecord, FeedbackRecord; model transcripts are not persisted —
  only results/summaries), `taxonomy.clusters` is deterministic (8 fixed
  categories, ≥ 2 evidence tuples except tool-misuse/human-correction),
  `CoachEngine.propose` makes ONE router call and mints
  `CoachProposalRecord` (before/after/rationale/evidence/signature) only
  when: custom agent, ≥ 3 runs, a cluster, a REAL provider (honest-mock
  guard copied from skill distill — under pytest it mints nothing), no
  pending/declined-within-14-days signature, and the revision passes
  `validate_revision` (non-empty, differs, growth ≤ max(1.2×, +160 chars),
  every never/always line kept). `accept` goes through the registry with
  `prompt_reason="coach"` (a revision lands) and is `stale` + 409 when the
  instructions changed since `before`. The coach is a platform service: it
  never runs as the agent and there is no self-coaching. Routes
  `routes/coach.py` (`GET/POST /agents/{name}/coach`, `/coach/proposals`
  accept/decline); event `coach.proposal`. CURATOR: SKILL.md frontmatter
  keeps extra keys (`created_by` user|agent|proposal, `created_session`,
  `pinned`, `archived_at`; unknown keys round-trip); `skill_create` stamps
  agent + session, a proposal approval stamps proposal ONLY when it creates
  a file (a refined user skill keeps its provenance); `inject()` reports
  names to `on_inject` → `record_injected` → `SkillStatRecord.inject_count`
  (separate from `use_count` = the agent chose to load it; written through a
  single-thread executor, never on the loop); discovery skips
  `<home>/skills/.archive/`; `SkillCurator.candidates` = user-root skills
  made by agent/proposal, unpinned, age ≥ 14 d, never used+injected or idle
  ≥ 30 d — the USER's own skills are never candidates; `sweep` tars
  `<home>/skills` to `backups/skills-<stamp>.tar.gz` first, then MOVES to
  `.archive/` (stamping `archived_at`), never deletes; curator rewrites
  restore the SKILL.md mtime so a pin does not make a skill young; loop
  `bg_tasks["skill_curator"]` (first sweep 1 h after boot, daily,
  `_tick("skill_curator")`, `_curator_stop`), gated by
  `config.curator_enabled` for the LOOP only. Routes
  `routes/skills_curator.py` registered BEFORE `agents.register` (its
  `GET /skills/{name}` catch-all would swallow `/skills/curator`); `GET
  /skills` rows carry `created_by`/`pinned`. Dashboard: `AgentFiles` +
  `AgentCoach` under the inbox in AgentDetail (custom agents only),
  `lib/diff.ts` (`diffLines` lifted from DocPreview, which re-exports it),
  Skills page provenance badge + pin + two-press Archive + the
  `SkillCurator` panel (dry run CHECKED by default), bell maps
  `coach.proposal`. Pins: `tests/test_agent_files_v1297.py`,
  `tests/test_coach_v1297.py`, `tests/test_skill_curator_v1297.py`,
  `dashboard/__tests__/agent-files-coach-v1297.test.tsx`,
  `dashboard/__tests__/skill-curator-v1297.test.tsx`.

- **A run has a TRUST posture, and every injected context is SCANNED**
  (v1.298.0, /goal wave 4a). TRUST: `core/trust.py` — TRUST_FULL/TRUST_LOW,
  `LOW_TRUST_DENY` (22 real tool names: create_agent, remember_preference,
  skill_create, assign_work, schedule_create, workflow_create, memory_write,
  ltm_append, notebook, memory_propose, capability_propose, pane_send,
  pane_spawn — plus the wave-4a review's tool_create, tool_delete, secret_set,
  webhook_add, sentinel_add, goal_add, worklist_add, workflow_run: a dynamic
  tool, a secret, a trigger, queued work, and a workflow whose steps start
  sessions the engine does not stamp low — and delegate_remote: a low run
  must not hand flagged content to another machine), `low_trust_overrides(base)`
  (deny wins, never widens),
  `taint_reason`, `effective_trust`. `Session.trust/trust_reason/tainted_at`
  (additive); `create_session(..., trust=, trust_reason=)` normalises;
  `inherited_trust(parent)` rides delegate, spawn_agent, assign_work (an
  assignment inherits ONLY when its source is `agent:<sid>` and that parent
  is low; user/retry rows are full), rerun and continue inherit the row.
  DOORS that start low: comm one-shot, comm chat lane and comm escalation
  (`config.comm_trust`, default "low"; `_chat_turn_kwargs` inspects the
  injected chat_turn's signature because 12 test fakes are 3-positional).
  Both chat lanes take `trust=`/`trust_reason=` (lock-step): under low they
  DROP the memory writers from the armed/auto/ask-armed sets with one ledger
  note, merge the denies into the overrides, and add ONE prompt sentence
  (`LOW_TRUST_PROMPT`) only when low from the door. TAINT: at the existing
  injection fence (runtime :2489, chat_turn, routes/chat) a flagged tool
  result lowers the run mid-run — `_lower_trust` writes the row off-loop,
  publishes `trust.lowered` {session_id, tool, category, reason} ONCE, and
  from then on the overrides carry the denies; nothing is appended to the
  prompt (cache) — the refusal (`deny_label="low trust"`) is the model's
  signal. `shell` under low trust gets `args["_isolate"]=True` (set AFTER the
  model's args so it cannot unset it; ledgered) and `sandbox/shell_tool.py`
  REFUSES a native fallback ("low trust needs the sandbox; Docker is not
  reachable", confinement "refused") instead of the advisory warning.
  BOTH chat lanes set `_isolate` too (`_low_trust_args`) — the reviewer
  found only the runtime did, while the Build pane and `body.tools` arm
  `shell` in the lanes. The chat lanes publish `trust.lowered` /
  `context.blocked` with session_id "chat" (no row): the bell links those
  to `/chat`, never `/sessions/chat`. A desktop reply INTO a phone thread
  (`POST /comm/threads/{id}/send`) stays FULL trust on purpose: the user
  typed the trigger. `blackboard_post`/`message_agent` stay allowed (the
  inbox is the run's own department, whose children inherit low).
  Receipts: `session_result` + `_session_view` (app.py AND
  routes/sessions._session_row) carry trust/trust_reason/tainted_at; the
  chat done frame / POST carries trust, trust_reason, trust_note. SCAN:
  `core/promptguard.py` — `scan_context(text, source=, cap=20_000)` (cap
  head 2/3 + tail 1/3 with a marker FIRST, then blank-line paragraphs, >1,500
  chars split on sentence/line, a flagged piece refined to the offending
  unit, replaced by `[BLOCKED: <category> — removed from <source>]`,
  consecutive placeholders collapse, fence markers defanged like the steward,
  excerpts masked with `detections.redact.mask`, a flagged SOURCE LABEL is
  withheld so the placeholder never re-plants the sentence), `guard(...)`,
  `publish_blocked` (`context.blocked` {session_id, source, count,
  categories} once per (session, source) per process; on a loop →
  create_task, off it → asyncio.run like the detections bell;
  `flush_published()` for tests), `guarded_project_text(project, …)`.
  Applied at: `projects/knowledge.ground` (name AND text), `AgentFiles.
  notebook_block`, `skills.framework.inject` + `guarded_instructions` (also
  the user-invoked `/skill` playbook, both lanes) — ONLY external-root or
  agent/proposal-made skills, never builtin or the user's own (their words),
  `documents/attachment_rag.rag_block` + `_prepare_attachments` (both lanes,
  off-loop), `memory/fabric.ground`, `learning.apply_to_prompt` (a flagged
  lesson is DROPPED, not placeholder'd), runtime `_project_context` via
  `guarded_project_text` + every seam's `event_bus=/session_id=`. The seams
  SCAN without a bus and PUBLISH only with one. Dashboard: `TrustChip`
  (SessionCard + session page + banner), TurnReceipt quiet lines (trust,
  trust note, blocked rows), bell maps `trust.lowered` and `context.blocked`.
  Pins: `tests/test_trust_posture_v1298.py`, `tests/test_promptguard_v1298.py`,
  `dashboard/__tests__/trust-posture-v1298.test.tsx`.

- **A standing grant is keyed on the EXACT arguments; a pack's new write
  tool is QUARANTINED; a schedule carries its own skills, folder, chain,
  pre-run script and skip-memory** (v1.299.0, /goal wave 4b). GRANTS:
  `core/grants.py` — `args_hash(tool, args)` (sha256 over tool + canonical
  JSON of the REAL args, never the redacted display), `StandingGrantRecord`
  (scope goal|agent|project|chat, scope_id, tool, args_hash — "" = any args,
  created ONLY by the goals ladder's per-tool offer, never by a card —
  label (redacted), expires_at default 30 d, revoked_at, uses),
  `GrantStore.match(scopes, tool, args)`. `permissions.authorize(...,
  grants=, scopes=)` lifts an ASK on a match AFTER the deny check (a deny
  is never lifted; a low-trust deny is a deny; `shell` only with an exact
  hash); the registry records `ToolInvocation.grant_id`. Scopes per run:
  goal (origin goal:<id>), agent (agent_name), project; the chat lanes add
  ("chat","chat"). An agent-scoped grant minted from an attended run
  covers that agent's UNATTENDED runs too (30 d; low trust still denies) —
  intended. `DELETE /goals/{id}` revokes the goal's live grants. Decision "always" (core/approvals.DECISIONS) = once +
  a grant on the strongest scope, in the runtime lane AND both chat lanes
  (lock-step); cards carry `can_always` + `args_hash`;
  `approval.requested` carries `args_hash` so the goals ladder
  (`ask_hash_stats_for` per (tool, args_hash); `ask_stats_for` keeps its
  shape) offers EXACT grants (`grant_offers_exact`, a new view field — the
  string `grant_offers` is unchanged) when the approved asks share one hash;
  `PATCH /goals/{id}/grants` takes `add` (names → compat JSON + a goal
  any-args row) and `add_exact` [{tool, args_hash, label}] + `expires_days`
  (null = never, goal scope only); the view lists `standing_grants`. The
  POST chat lane never cards (every armed tool is its own grant), so only the
  stream lane and the runtime answer "always". `platform.grants` is attached
  in platform.py and `bind_loop`ed in the lifespan so a sync route's revoke
  still publishes. Routes
  `routes/grants.py` (list, revoke); events grant.created/revoked.
  QUARANTINE: `mcp/manifest.py` persists `<home>/mcp/manifests/<server>.json`
  per pack; `from_spec` reads MCP annotations (readOnlyHint → READ); the
  FIRST load trusts everything (the user installed it knowingly); a tool that
  APPEARS LATER or changes shape and is write-like registers
  `Tool.quarantined=True` — the platform's MCP auto-approve wrapper answers
  ASK for it regardless of pack/global auto_approve, and `authorize(
  quarantined=True)` is lifted by NO blanket grant (the shared `mcp_call`
  key in session_allow, a standing grant) but IS lifted by the tool's OWN
  name in session_allow — i.e. a card answered about that tool runs it (the
  reviewer found the first cut discarded the user's Allow); `perm_key()`
  arms a ContextVar the wrapper reads and `registry.invoke` clears it in a
  `finally`; the ask carries `quarantined: true` and `can_always` is false
  for it; read-only newcomers are
  never quarantined; an upgrade's first boot writes every pack's manifest
  and trusts what is there; `record=False` probes never touch the manifest; `POST
  /mcp/servers/{name}/tools/{tool}/trust` clears it. SCHEDULES: payload keys
  `skills` (validated; `with_skills` COPIES the definition — never mutate the
  shared builtin), `workspace_root` (usable_workspace_root; a folder rules
  file AGENTS.md/.ironjarvis.md injects through promptguard cap 8,000, only
  when the fire set `options.folder_rules`), `context_from` (another
  schedule's last session summary ≤ 4,000, scanned; self-reference 422),
  `script` {command, timeout_s ≤ 120, cwd} run pre-run through the shell
  tool's confinement, output head+tail 8,000 scanned — REFUSED from the
  agent-made `schedule_create` tool, `skip_memory` (Session.options_json →
  the runtime skips lessons/memory index/fabric INJECTION only);
  `PATCH /schedules/{name}` (payload_set/payload_unset, re-validates,
  re-arms); GET rows carry the knobs. Dashboard: "Always allow exactly
  this" on cards (only with can_always), `StandingGrants` on Autonomy,
  quarantine badge + Trust on Tools, schedule knobs + Edit on Schedules,
  bell maps grant.revoked only. Pins: `tests/test_standing_grants_v1299.py`,
  `tests/test_mcp_quarantine_v1299.py`, `tests/test_schedule_knobs_v1299.py`,
  `dashboard/__tests__/{standing-grants,mcp-quarantine,schedule-knobs}-v1299.test.tsx`.

- **claude-cli speaks the CLI's own protocol, behind a one-request relay**
  (v1.300.0; design from NousResearch's MIT hermes-plugin-claude-subscription-
  directsdk @ ef73726). `providers/adapters/claude_native/`: `frames.py`
  (pure: history -> stream-json frames — every earlier user frame carries
  `shouldQuery:false` and is acknowledged by a zero-turn `result` before the
  next is sent; images ride the user frame; tool names -> `mcp__ij__<safe id>`,
  reversible, <= 55 chars, hashed on collision; an assistant turn is replayed
  from its `claude_cli_native` carrier ONLY when its text + calls still equal
  the carrier's projection, else rebuilt with unique ids), `transport.py`
  (argv, env, temp files, the relay, the reader thread, `assemble`),
  `admission.py` (loopback relay: ANTHROPIC_BASE_URL points at a per-call
  secret path; it forwards the FIRST `/v1/messages` and answers every later one
  locally with `IRONJARVIS_MODEL_ADMISSION_CONSUMED` — the CLI's own retries
  and side requests never reach the plan — and `pin_message_breakpoint` moves
  the one message `cache_control` onto content the next request replays
  unchanged), `inert_mcp.py` (lists the manifest, REFUSES every call; Iron
  Jarvis executes tools). A tool batch is SUCCESS when the result is
  `error_max_turns` with exit 1 (`--max-turns 1`). The env is
  `claude_models.child_env` (strips every `ANTHROPIC_*`, Bedrock/Vertex/Foundry
  switches and `CLAUDE_CODE_EXTRA_BODY`, logs the NAMES once — claude-cli means
  "my subscription", so a stray API key is ignored, never obeyed and never a
  refusal) plus the no-retry/no-compact/no-tool-search knobs. `--mcp-config`
  is a FILE (JSON on argv does not survive a `.cmd` shim). Frozen, the inert
  server is `ironjarvis.exe claude-inert-mcp <manifest>`, served by the
  FAST PATH in `packaging/ironjarvis_entry.py` before the Typer import (the CLI
  waits for this server on every call; the daemon CLI import is ~0.9 s) — keep
  it identical to the hidden subcommand. Timeouts: the IDLE watchdog (180 s,
  reset by every event) is the real bound; `_CLAUDE_TOTAL_TIMEOUT_S` (1 h) is
  only a safety net — the old 240 s whole-call cap (`_TIMEOUT_S`, still Codex's)
  cut off answers that were still streaming. CACHE, measured live: round 1 of a
  tool loop is never reusable (the CLI appends date/e-mail reminders to the
  turn it answers and drops them on replay); from round 2 the relay's pin reads
  ~90% of the prompt from cache. MODELS: `providers/claude_models.py` reads the
  live picker from the CLI's `initialize` control request (no model call),
  caches it 10 min in process + on disk (`<home>/claude_models.json`), never
  blocks a route (`catalog()` refreshes in the background) and never spawns
  from routing (`_known()`); `native_model` keeps `[1m]` for 1M models, refuses
  `[1m]` on a 200K one in a sentence, and maps subscription/default/"" to the
  live default row when known (else no `--model`). `_context_window_source`
  has a `catalog` rung for claude-cli (and an anthropic provider inheriting
  it). The suite must never run the REAL handshake: a session fixture in
  `tests/conftest.py` disables `_run_handshake` (a background refresh once
  landed this PC's live catalog inside a later test). COST: usage
  `input_tokens` is the TOTAL prompt incl. cache, the cache buckets are
  optional keys, `cost_usd` is the CLI's own figure; `pricing.step_cost`
  prefers it, `recorded_cost` reads a stored `cost_usd` before re-pricing
  tokens; `Session.cost_usd`/`AgentRun.cost_usd` hold it, allowances, goals
  and the digest bill it — but it is a LIST-PRICE EQUIVALENT under a
  subscription: `is_list_price_equivalent` flags it, usage totals keep it in
  `list_price_equivalent_usd` (never `cost_usd`), fleet keeps it out of cloud
  spend, and the receipt says "list" only when flagged. The flag is
  MODEL-aware: `is_list_price_equivalent(provider, model)` is False for a
  model that draws pay-as-you-go usage credits (`draws_usage_credits`, read
  through `claude_models._known()` — pricing must NEVER spawn the CLI), so
  that money is billed everywhere; pass the model at every call site. The
  `llm.completed` event carries `list_price_equivalent` and the Activity
  timeline sums metered money only (list-price value on its own line); the
  goal digest writes its own `spent.words` ("≈$x at list price (Claude
  subscription)"). Upgrade day: an OLD subscription row with no stored cost is
  $0 in `recorded_cost` (allowances, usage rollup and goals all read it) —
  those runs were free under the rules then. claude-cli's table fallback
  prices cache writes at the 1-HOUR rate (2×; the CLI writes `ttl: "1h"`).
  ERRORS: no HTTP status, or a 200 cut off before any text, is
  `ProviderError(transient=True)` in plain words (the CLI's own retries are
  off and the relay refuses them, so the router must see it as transient);
  a drop after text keeps the committed path; 429/5xx keep their status +
  Retry-After; a 401 or an auth-shaped 403 is the sign-in remedy and tells
  the probe, a model-access 403 is not — NEVER read the CLI's own
  `authentication_failed` code for this: the real CLI stamps it on EVERY
  upstream 403 (measured), so Anthropic's error type and words decide, and
  the remedy quotes Anthropic, never the relay's refusal marker. SIGN-IN
  RETRY: after a 401 (or a 403 typed `authentication_error`) the real CLI
  refreshes its login and resends; the relay admits exactly THAT one retry
  (`Admission._reauth_allowed` — a refusal spent nothing, so it cannot
  duplicate work) and records a refusal as answered BEFORE relaying it, so a
  fast retry cannot race the decision. Measured live: 401-then-OK answers. PROXY: the relay honours
  HTTPS_PROXY/ALL_PROXY/NO_PROXY from the ENVIRONMENT only (CONNECT tunnel,
  TLS still verified on the upstream name), and the CLI child's NO_PROXY
  gains 127.0.0.1,localhost so its plain-HTTP call to the relay never goes
  out through a corporate proxy. MODEL ROWS: a keyless anthropic lists the
  live picker rows THEN the curated rows the picker does not cover (pinned
  templates must keep resolving; a curated id FOLDED into the live row that
  covers it, like `claude-haiku-4-5` under the dated row, rides that row's
  `aliases`, which `templates.analyze_requirements` accepts); the goals strip
  says "used", not "spent"; the dial's `familyVersion` reads both id
  shapes and never a date or `[1m]`. Pins:
  `tests/test_claude_native_v1300.py` (+ `fixtures/fake_claude_v1300.py`),
  `test_claude_native_errors_v1300.py`, `test_claude_admission_v1300.py`,
  `test_claude_models_v1300.py`, `test_claude_catalog_rows_v1300.py`,
  `test_claude_context_window_v1300.py`, `test_subscription_cost_v1300.py`,
  `test_subscription_cost_followups_v1300.py`,
  `test_claude_native_followups_v1300.py`,
  `dashboard/__tests__/{claude-native,model-dial,subscription-cost-followups}-v1300.test.tsx`. RELEASE CHECK: run
  `"<install>\resources\daemon\ironjarvis.exe" claude-inert-mcp <manifest>`
  and feed one `initialize` line — one JSON-RPC line must come back.

- **Iron-Proxy is the ACCOUNT MANAGER; Iron Jarvis still EXECUTES** (v1.301.0;
  RealDealCPA-VR/Iron-Proxy, MIT, user decision 2026-10-03 "shared accounts").
  Iron-Proxy's own Claude lane is one prompt, no tools, no images — routing
  Iron Jarvis's traffic THROUGH it would undo v1.300.0. So Iron-Proxy only
  answers WHICH account (`GET /iron/pick` -> profile + the vendor CLI's home
  env) and is TOLD what happened (`POST /iron/profiles/:id/signal` with the raw
  status/allow-listed headers/text — ITS classifier parks; `/finished` records
  usage). `iron_proxy/accounts.py`: claude-cli -> anthropic/CLAUDE_CONFIG_DIR,
  codex-cli -> openai/CODEX_HOME, grok-cli -> xai/GROK_HOME; only the HOME var
  is applied (after `claude_models.child_env`), protected names (PATH, TEMP…)
  are never unset; a retry with the next account happens ONLY when Iron-Proxy
  answers `parked: true` and nothing reached the caller, at most once per
  account and 5 attempts; OVERLOAD (500/502/503/504/529, "overloaded") is not an
  account problem — never reported, never rotated, raised exactly as v1.300.0
  (transient); CODEX hands Iron-Proxy only its own `ERROR:` lines (no
  last-line fallback — it can be the model's answer), never a line of the prompt (`cli_error_lines` — codex echoes the prompt
  into stderr, and a prompt saying "credit balance" parked every account for
  24 h as `billing`); all-parked / needs-sign-in / refused-token / too-old /
  unavailable are non-transient status-less ProviderErrors worded BY KIND
  ("request limit" / "usage limit" / names each account in a mix — never the
  word "rate-limited", which the router's transient regex matches) and carry
  `no_failover = True`: `router.is_no_failover` raises them at the top of
  BOTH complete() and stream() error paths (no pin fallback, no default
  fallback, no sideways failover) and `is_transient_error` is False for them
  (so the same-adapter retry and app.py's one-shot failover skip them too) —
  the "never another provider" promise on the card and in the Handbook rests
  on this. ONE EXCEPTION, the user's standing one: an AUTO turn
  (`(provider or default_provider) == "auto"`) may substitute sideways and is
  disclosed as `failover` with `from`. A no_failover error never strikes the
  breaker (parked accounts are not an unhealthy provider), Auto included.
  ONCE A LEASE EXISTED and the failure was an ACCOUNT signal (401/403/429 or
  Iron-Proxy's billing/sign-in/limit words), the error that leaves the
  adapter is ALWAYS no_failover — Iron-Proxy could not be told ("…hit a limit
  and Iron-Proxy could not be told — try again shortly"), declined to park,
  re-picked a tried account, or the 5-attempt cap hit (one more pick fetches
  the by-kind sentence); unmarked raw re-raises of an account 429 once took a
  151 s retry ladder and then failed over to codex. Iron-Proxy ON never silently uses this PC's own
  login: the lease goes through `service.lease_client(timeout_s=15)` (waits
  for a start, starts it at most every 30 s, raises `IronProxyUnavailable`
  when down / OUTDATED (health lacks `features: ["executor-v1"]`) / token
  refused). With a lease a sign-in refusal never touches
  `cli_auth.DEFAULT_PROBE`; every pick/signal/finished runs off the loop and a
  reporting failure never fails a turn. Iron-Proxy OFF or NO_PROFILE for that
  provider = byte-identical to v1.300.0. AVAILABILITY: `ProviderManager.
  iron_proxy_usable` (wired to `has_usable_account`, a CACHED snapshot the
  watch loop and every account route refresh — `available()` never calls
  Iron-Proxy) keeps claude/codex/grok available when this PC's own login is
  signed out but Iron-Proxy holds a usable account. The admission relay reads
  a request's body BEFORE any refusal (404 / 400 CONSUMED) — replying over
  unread bytes RST the socket on Windows (WinError 10053, ~1 in 12).
  RUNTIME: `platform.iron_proxy` (`iron_proxy/service.py`) reuses a running
  proxy found via `<IRON_PROXY_DATA_DIR or ~/.iron-proxy>/proxy.json` (pid
  alive + /iron/health), else spawns the vendored bundle `serve --port 0`
  with `IRONJARVIS_IRON_PROXY_NODE` + `_BUNDLE` (desktop/main.js passes
  Electron's exe + resources path only when the file exists; the child gets
  `ELECTRON_RUN_AS_NODE=1`), stops only an OWNED child, keeps the token inside
  the daemon (never in a route, log or error), recognises its OWN child after a
  daemon crash (`<home>/iron-proxy-owned.json`: pid + create time) so Disable
  still stops it; `Config.iron_proxy_enabled` is
  persisted by the enable/disable routes ONLY (not a settings key — a settings
  write would flip it without starting anything). Routes `/iron-proxy*`
  (status, accounts CRUD, adopt, reorder, unpark, signin = a Build pane running
  the account's login command, signout); the card is
  `components/connections/IronProxyCard.tsx`. THE BUNDLE: `desktop/vendor/
  iron-proxy/iron-proxy.mjs` is produced ONLY by `scripts/vendor_iron_proxy.py`
  from a CLEAN Iron-Proxy commit (SOURCE.txt: commit + sha256, pinned;
  `.gitattributes` `-text` so autocrlf never rewrites it); push the Iron-Proxy
  commit BEFORE the Iron Jarvis one so SOURCE names a public commit;
  electron-builder ships it to `resources/iron-proxy` and afterPack fails the
  build when it is missing and inventories it. Pins:
  `tests/test_iron_proxy_{service,routes,accounts,vendor}_v1301.py` (+
  `fixtures/fake_iron_proxy_v1301.py`; `accounts.plain_hint` rewrites every
  Iron-Proxy hint that names its CLI or switcher into a whole Connections-page
  sentence, table-pinned against the VENDORED bundle's DEFAULT_HINTS),
  `dashboard/__tests__/iron-proxy-card-v1301.test.tsx`.

- **A Build pane's ACCOUNT is chosen when the pane STARTS, and shown on it**
  (v1.302.0). A vendor CLI signs in once, at process start, through its home
  var (CLAUDE_CONFIG_DIR / CODEX_HOME / GROK_HOME) and then talks to its vendor
  directly — no app can move a running session, and Iron-Proxy never handles
  tokens. So the account is the pane's SHELL ENV at spawn, never a `$env:…=`
  typed into a live shell. `terminals/pane_accounts.py` resolves
  `TerminalCreate.accounts {provider: "<profile id>" | "default"}`: explicit id
  → Iron-Proxy `GET /iron/pick?profileId=` (ONLY when health `features` has
  "pick-profile" — an older proxy ignores the id and hands out the next ready
  account), applied with `iron_proxy.accounts.Lease.apply` (one rule, not a
  fork) inside `_with_pane_env` (daemon creds stripped, account, then pane
  identity); "default" REMOVES any inherited home var (really this PC's
  login); a key absent + Iron-Proxy on → the first free account (chat's order);
  Iron-Proxy off → a NEW pane is byte-identical to v1.301.0 (no `accounts`
  key); a RESTORED pane that recorded an account keeps that account's folder
  and row even with Iron-Proxy off (deliberate — its conversations live there).
  An ADOPTED account whose home is the CLI's own default (`~/.claude`) sets NO
  home var, in panes AND in the v1.301.0 chat lease: with CLAUDE_CONFIG_DIR set,
  Claude Code reads `$CLAUDE_CONFIG_DIR/.claude.json`, not the user's real
  `~/.claude.json` (MCP servers, projects, trust) — measured here. A pane
  strips only the CHOSEN provider's own key names, never the user's other keys
  (the v1.217.0 rule); a plain "+" pane picks its first free account from the
  CACHED snapshot (no Iron-Proxy call); the Launch menu reads the light
  `?discover=0` view (the full view makes Iron-Proxy run every vendor CLI's
  status command).
  An unusable EXPLICIT account is a 409 sentence by kind and NO pane — never a
  different account; Iron-Proxy not answering for an absent key → this PC's
  login WITH a `note` (a plain "+" uses the CACHED snapshot; no snapshot = "not answering yet" note). Rows (`GET /terminals`, create/launch/PATCH answers AND
  `/terminals/activity`) carry `accounts` with live state from the CACHED
  snapshot (`active|ready|parked|needs-sign-in|missing(+reason disabled)|
  default|unknown`) — list routes never call Iron-Proxy, and the Build page
  reads GET /terminals once, so the activity rows are what keep the chip live.
  Restore applies the RECORDED home (a stable folder) with no Iron-Proxy call
  — boot must never wait on it — and verifies off the boot path.
  `POST /terminals/launch {cli, account?, near?}` opens a NEW pane on that
  account in `near`'s folder and presses Enter (the click is the consent);
  `POST /iron-proxy/accounts/{id}/open` is the card's one-press version.
  Dashboard: Launch rows "as <account>" (`launchOffer` — Iron-Proxy off = the
  menu is exactly v1.301.0's; it reads /iron-proxy only while the menu is
  open), `PaneAccountChip` (`#pane-account-<id>`), the rail label, and
  `adoptPane` on the Build page (a `router.push` with only a new `?focus=`
  keeps Next's page state, so it would neither show nor focus the new pane).
  Pins: `tests/test_build_accounts_v1302.py`,
  `dashboard/__tests__/build-accounts-v1302.test.tsx`.

- **Continue on the next account is DETECTED from Claude Code's own limit
  record, CHOSEN by session id, and COPIED, never moved** (v1.303.0). Evidence
  on this PC (claude 2.1.288): real account limits read "You've hit your
  session limit · resets 3:45pm (America/New_York)", "You've hit your weekly
  limit · …", "You're out of usage credits …" (the binary's classifier prefixes
  "You've hit your", "You've reached your", "You're out of usage credits",
  "Your org is out of usage credits"), and each is also written into the
  session jsonl as `isApiErrorMessage: true, error: "rate_limit"` with
  `sessionId`, `cwd`, `timestamp`, `quotaLimits`. "Context limit reached" is
  the CONTEXT WINDOW, not an account — never a limit (it parked a healthy
  account in the first cut). `terminals/limit_state.py` is an ALLOW-LIST of
  account-limit shapes at line start inside an allow-listed frame; `>`/`❯`
  prompt lines and `⏺`/`●` answer lines are never the hit (they are the
  user's / the model's text); a later answer or working line clears it.
  Iron-Proxy gets ONLY `PaneLimit.signal_text()` (the matched message + a
  normalised reset suffix), never other scrollback. WHICH conversation
  (`terminals/continue_on.py`): every Claude WE start is typed `claude
  --session-id <uuid4>` and the id is recorded on the pane (snapshot; restart
  resumes `claude --resume <id>`); else only a session whose LAST record is a
  rate_limit error with the pane's cwd and a fresh timestamp, exactly one —
  several → fresh + note; NEVER newest-by-mtime (two panes on one account hit
  the same limit at the same moment). Located by glob
  `<home>/projects/*/<id>.jsonl` and copied into the SAME-NAMED folder of the
  next account's home (Claude truncates/hashes long encoded names — never
  recompute them), never overwriting a DIVERGED file, links skipped, confined
  to the home. `--resume` KEEPS the session id (`--fork-session` would change
  it), so a round trip A→B→A meets A's OLDER copy of the same id: a byte-PREFIX
  destination is the same append-only conversation and is replaced (old bytes
  copied to `<home>/trash/<stamp>/claude-carry/…` first, then temp +
  `os.replace`); only a true divergence refuses. A recorded id counts only
  while it is the NEWEST conversation in its folder (the user may /clear into
  a new id): restart Resume falls back to `claude --continue`, continue to the
  unique-limit-record scan. `tzdata` is declared for win32 (zoneinfo named
  zones; pyinstaller-hooks-contrib's hook-zoneinfo/hook-tzdata ship it).
  Every refusal (this PC's login, pane cap 429, Iron-Proxy off/unavailable, no
  other usable account) runs BEFORE any signal or copy. The strip says before
  the press that the conversation is COPIED to the other account (work →
  personal privacy) and Dismiss keys on the limit's `since`, not its text (a
  countdown repaints). Pins: `tests/test_build_continue_v1303.py`,
  `dashboard/__tests__/continue-next-v1303.test.tsx`.

- **A Build pane and a `claude` child are TOP-LEVEL sessions: a parent Claude
  Code session's markers never cross** (v1.303.1, found by the LIVE end-to-end
  run). A daemon started from inside a Claude Code session (a dev shell, an
  agent) handed `CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`,
  `CLAUDE_CODE_MESSAGING_TOKEN`/`_SOCKET`, `CLAUDE_CODE_SESSION_ID`,
  `CLAUDE_PID`, … to every pane and child; Claude Code in the pane printed
  "Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker"
  (no conversation file → Continue had nothing to carry) and the parent's
  messaging TOKEN reached shells it was never meant for.
  `core/claude_markers.drop_claude_session_markers` runs in
  `terminals.manager._with_pane_env` and `claude_models.child_env`
  (case-insensitive). The user's own Claude settings (CLAUDE_CONFIG_DIR, …)
  are not markers. LIVE-PROVEN the same day (real ConPTY, real claude 2.1.289,
  Claude Max): a pane on the ADOPTED ~/.claude starts `claude --session-id`,
  answers, saves its transcript, has no CLAUDE_CONFIG_DIR and keeps other keys;
  the shipped `carry_over` copied that real jsonl byte-identical into a second
  account's home and `claude --resume <id>` there got past lookup to "Not
  logged in" — the only unproven step is a SECOND SIGNED-IN account's API
  accepting it (Anthropic documents thinking signatures as portable across
  platforms). Pin: `tests/test_claude_session_markers_v1303.py`.

- **A carried conversation the next account refuses ends in a HANDOFF, never a
  dead end** (v1.303.2). Whether a second signed-in account accepts the first
  one's transcript cannot be proven without one, so the product does not
  depend on it. The pane opened by Continue records `continued_from`
  (`from_title`, `session_id`, `carried_path` = the copy in the NEW home, `at`).
  ONLY THE FILE raises a problem (`terminals/resume_failed.py`): a new exact
  `isApiErrorMessage` record after `at` in that copy (bounded tail read, only on
  size/mtime change, in the sync activity route) — the SCREEN only clears,
  because tool output under `⎿` ("⎿ API Error: 400 from upstream", "token has
  expired") read as Claude's own error and would /exit a working Claude. A
  failure needs a 4xx other than 429 (limit flow) / 401 (sign-in), or
  invalid_request / "does not have access" / "prompt is too long" / signature
  words — timeouts, "fetch failed", a 408 and billing/server/max-output errors
  (even with a 400) are not; a login expiry ("Login expired ·
  Please run /login", by a 401 OR sign-in words, never the 403
  `authentication_failed` code) is `sign_in_needed` with the account id. `POST /terminals/{id}/start-
  fresh-with-handoff` (`terminals/handoff.py`): refused while the pane is
  working/blocked; a deterministic handoff (first ask ≤ 1,500, last 6 text turns
  ≤ 800, tool_result payloads excluded, ≤ 30 paths, ≤ 8,000 total, secrets
  MASKED with `detections.redact.mask`) at `<home>/handoffs/<sid>/handoff.md`
  (one folder per conversation; others older than 30 days pruned; listed in the
  storage report; included, masked, in backups; never the project); then Ctrl+C (clears Claude Code's
  composer, multi-line drafts included — measured live on 2.1.289; Esc Esc can
  open the rewind menu, Ctrl+U clears one line), `/exit`, Enter, wait for the
  pane's OWN shell prompt (15 s; else a 409 "could not see the shell prompt …
  if Claude has exited, run: <line>"), then ONE line `claude --session-id <new>
  "--add-dir=<that conversation's folder>" "Read the handoff…"`. `--add-dir
  <directories...>` is VARIADIC on 2.1.289 — the two-token form swallows the
  prompt (measured). LIVE-PROVEN on the real signed-in login: the fresh session
  answered a fact only the handoff carried. Also fixed by the live run:
  `session._ANSI_RE` now strips CSI with `<=>!` params and `ESC 7/8/=/>` (Claude
  Code's exit left `>4m<u` before the prompt), and ConPTY glues the prompt onto
  the row it overwrites, so pwsh/cmd prompts are matched at the line END.
  Shared quoting: `routes/iron_proxy.shell_line(shell, argv)`. Pins:
  `tests/test_build_handoff_v13032.py`,
  `dashboard/__tests__/start-fresh-v1303-2.test.tsx`.

- **Iron-Proxy is TOLD to re-check a sign-in; it never notices by itself**
  (v1.303.3, a USER BUG). The Sign in pane runs the account's login outside
  Iron-Proxy's own login session, so the login landed (`.credentials.json`
  written, `claude auth status` loggedIn: true) while Iron-Proxy's state stayed
  `unauthenticated` from the account's creation — the card said "Needs sign-in"
  forever and the user looped. Now: `IronProxyClient.refresh` (POST
  /iron/refresh); `GET /iron-proxy` re-checks an unauthenticated account ONLY
  while a sign-in is IN PROGRESS for it (an open `signin_for` pane, or Sign in
  pressed in the last 3 minutes) — Iron-Proxy's refreshStatus marks `ready`
  whenever `claude auth status` reads the LOCAL credentials file as logged in,
  so re-checking every signed-out account flipped a 401-revoked account back
  to ready within 10 s and into a 401 loop (caught in review); the sign-in pane carries `signin_for` and
  `terminals/signin_watch.py` spots Claude Code's own "Login successful."
  (claude 2.1.289 binary; "Authentication successful" is the browser/MCP page,
  not matched — only the exact body `Login successful.` or `Login successful.
  Press Enter…` under `Logged in as …`; a `⎿`/`>`/`❯` row or an `echo` never
  counts) → the FIRST sighting forces a refresh, later ones are throttled,
  ≤ 6 per pane → `signed_in` only on Iron-Proxy's `ready`; the card read never
  waits (`wait_s=0`), only `/check` does;
  `POST /iron-proxy/accounts/{id}/check` re-checks now. The second "login" the
  user saw was Claude Code's ONE-TIME first-run welcome in a new config dir
  (credentials written once — timestamps), so the sign-in pane says so. SAME
  BUG: a proxy started by an EARLIER Iron Jarvis (a source run, old bundle
  without "pick-profile") was reused forever with no error; a located proxy
  that lacks a feature the bundle advertises is REPLACED only when its command
  line is an Iron Jarvis bundle (`iron_jarvis_owner`: the FIRST script argument
  is `iron-proxy.mjs` in THIS repo's `desktop/vendor/iron-proxy` or in an
  install's `resources/iron-proxy` whose install folder holds `Iron Jarvis.exe`)
  — anything else (the tray app, `iron-proxy serve`, another clone) is never
  stopped and gets the OUTDATED sentence. Replacement is at most once per
  (bundle path, version) and 2 per 10 min — two Iron Jarvis copies sharing
  `~/.iron-proxy` (a source run + the installed app) otherwise killed each
  other's proxy forever; past the limit status.error names the other copy. A
  kill is CONFIRMED (pid dead) before proxy.json is removed or a new proxy
  spawned; else "could not stop the older Iron-Proxy (pid N)". A packaged
  daemon cannot recognise a source run's proxy (no repo path when frozen): it
  only says OUTDATED.
  Verified live on the user's install: the packaged app started its own bundle
  as `Iron Jarvis.exe …\resources\iron-proxy\iron-proxy.mjs serve` (owned,
  pick-profile). Also: `continue_on` compares bytes directly — `filecmp.cmp`
  caches by (size, mtime) and judged a same-size rewrite unchanged. Pins:
  `tests/test_iron_proxy_signin_v13033.py`,
  `dashboard/__tests__/signin-check-v1303-3.test.tsx`.
  SAME RELEASE, found by the full suite under load: (a) the Build pane CAP
  counted only REGISTERED panes, so creates still spawning all passed it (16
  panes against a cap of 5) — `TerminalManager.create` now reserves its slot
  (`_reserved`) in the same lock hold as the check and counts `_pending_panes`
  + reservations; pin `test_terminal_cap_counts_creates_still_in_flight` (a
  slow backend makes it deterministic). (b) `comm/prompts.newest_open`
  ordered by `created_at` alone — the v1.286.0 same-tick rule — and failed its
  pin 2 runs in 3; tie-break on `rowid DESC`.

- **The Agents page is a set of PROJECT WORLDS; a project's room is
  grounded in that project and private to its table** (v1.304.0). The user:
  "make the agent profile images more prominent" and "a round table for each
  set of agents, grouped by the project … tabs for board, items pending the
  user, completed tasks, new tasks". BACKEND: `AgentThreadRecord.project_id`
  (additive, indexed in `_HOT_INDEXES`) — `GET /agents/threads` with no
  `project_id` lists ONLY rooms with none (the General world is today's page);
  `Project.team_json` holds the curated team (roster names; remote and the
  supervisor allowed; more than 24 (`TEAM_MAX`) refused BEFORE matching; an
  ambiguous bare name is refused). `projects/world.py` is the one place for team validation, seating,
  suggestions (agents with work in the project in 30 days, not seated, newest
  first, at most 6, each with a `why`), and the Waiting / Completed buckets:
  `needs_you` and interrupted runs are WAITING (never `done_7d`, never
  Completed), as are paused asks (NUMBERS, never arguments) and blocked/held
  assignments. Routes: `GET /agents/worlds` (active projects by last
  activity + `general.thread_count`), `GET|PUT /projects/{id}/team` (PUT
  re-seats the room server-side), `GET /projects/{id}/world`,
  `POST /projects/{id}/world/room` (201 created / 200 exists / 409 no team).
  `DELETE /projects/{id}` untags its rooms. GROUNDING (`run_round`): a project
  room's speakers get `project_room_block` (the project's context through
  promptguard) — but a REMOTE seat gets only the user's messages and its own
  lines plus one sentence saying so (member rows carry `sees: "only your
  messages"`), a seat with no model pin answers on the project's default model
  (when the project has one), and a seat pinned to a different PROVIDER than a
  LOCAL project default gets no block, sees only the user's lines and its own,
  and is flagged `ungrounded: true` + `ungrounded_reason: "runs on <provider>"` (the
  v1.162.0 rule: a local project's files never reach a cloud model by a
  seat's pin). `for_chat(adopt=)` adopts only rooms with NO project.
  DASHBOARD: `components/agents/TableSeats.tsx` (`seatLayout(n, width)`: a
  solid elliptical table, 72 px seats outside the rim, one ring up to 6, a
  second ring past that, a 56 px strip on narrow screens; the speaker lifts
  with a glow; status ring from roster health + the live round; reduced
  motion respected); message portraits 40 px; `AgentSeatModal` / AgentsModal
  hero 160 px; the inbox defaults to the room's project. `app/agents/page.tsx`
  routes between `WorldsGrid` (one card per project, non-overlapping
  `TeamFaces` with "+N"), General (`GeneralAgents` = the old page) and
  `WorldView` (`?project=<id>`; room created lazily; RoundTable takes
  `projectId`/`projectName` and NEVER creates a project room itself) with
  `WorldTabs` Board / Waiting on you / Completed / New task (`ProjectTasks`
  with `assigneeChoices` = the team + "Whole team — Jarvis decides") and
  `TeamEditor` (saving re-seats; says what a remote seat sees). A 404 from an
  older daemon renders today's page. Pins: `tests/test_agent_worlds_v1304.py`,
  `dashboard/__tests__/agent-faces-v1304.test.tsx`,
  `dashboard/__tests__/agent-worlds-v1304.test.tsx`.

- **A preference has a STATUS, and only a confirmed one reaches a prompt;
  the second same correction becomes ONE question** (v1.305.0; ideas from
  agent-personalizer, MIT, Auny LLC — no code taken). `LessonRecord` gained
  nullable `status`/`origin`/`evidence_json`/`signature`/`decided_at`;
  `learning/models.lesson_status` is the ONE place NULL reads as confirmed
  (never rewritten on disk), and `learning/engine.confirmed_clause()` sits
  inside `lessons()` — the single query every reader goes through
  (apply_to_prompt, recall_lessons, fabric, graph, overview, /lessons, cli);
  `improvement/engine.py`'s raw select filters too, and a grep pin lists
  every `.lessons(` caller and raw `select(LessonRecord)` so a new reader is
  caught. `remember_preference` ("from now on …") is unchanged: confirmed,
  origin said, the v1.282.0 receipt. `learning/corrections.py` is pure and
  makes NO model call: `is_correction` needs a correction shape AND a style
  target and refuses task verbs, made things, questions, code, links, a
  correction scoped to one case (`_ONE_OFF`: "for this client", "this time",
  "on my phone"), > 280 chars, > 3 lines (table tests: 42 match / 58
  must-not); `signature` folds
  synonyms and keeps polarity; `similar` = Jaccard ≥ 0.5 + same polarity + a
  shared content token; `proposal_text` builds the sentence from the user's
  OWN words. `learning/preferences.suggest_for_turn` is called by BOTH chat
  lanes (lock-step; only POST /chat and /chat/stream ask, as
  `suggest_preferences=not body.pane_id.strip()` — the sidebar, phone,
  agents AND a Build pane's turn get null: PaneChat renders no line, so a
  pane-minted row would be an unseen question holding an open slot; the
  pane's saved thread still counts as evidence for the main chat): the detector runs
  first, only a correction pays the look-back (`asyncio.to_thread` inside
  `wait_for(2 s)`, wrapped, logs no text) over 30 days of chat + phone lines
  in a DIFFERENT turn (a saved thread matching the body's user lines is this
  conversation — ChatBody carries no thread id); it mints a `proposed` row
  (max 3 open, `mint` serialised) and puts `suggestion` on the done frame /
  POST response ALWAYS (null when none). Declined is FINAL (`_blocked` on the
  signature) until "Ask again" deletes the row; a kept or stated preference
  also blocks. Routes `routes/preferences.py`: `GET /memory/preferences`
  (kept/suggested/never/open_limit/scan.sources), keep {text?} / decline /
  ask-again / PATCH / DELETE (edited text 1–280 chars, promptguard-scanned),
  `POST /memory/preferences/scan {sources}` — consent PER PRESS, the user's
  typed prompts only from each session's OWN file (the history reader's
  subagent fold holds the parent agent's prompts, not the user's; a record
  that starts with markup is the CLI's own — `!` mode stores
  `<bash-stdout>` OUTPUT as a user record — and is skipped), newest 20
  per source, mints only when a Claude Code/Codex line is in the cluster.
  Events preference.suggested {via: chat|scan} / kept / declined. DASHBOARD:
  `lib/preferences.ts` (decoders OUTSIDE api.ts; `decodeSuggestion` is the
  stream hook's whitelist), `components/chat/PreferenceSuggestion.tsx` (the
  quiet line under TurnReceipt: Keep · Edit · Not this; Enter/Escape never
  bubble to the composer; never takes focus), stored on `ChatMessage.
  suggestion` by BOTH lanes; `settledSuggestionsRef` + `applySettled` inside
  `queueSave` so a Keep pressed mid-turn survives that turn's save; a
  keep/decline answered 409/404 (decided on the Memory page / another
  window) re-reads `GET /memory/preferences` and settles to the REAL outcome
  (`settledElsewhere`: kept with the kept words, declined, or `state: "gone"`
  which renders nothing) through the same `onSettle` save — only another
  error (400 flagged, network) shows the sentence;
  `components/memory/PreferenceSections.tsx` (#prefs-kept/-suggested/-never/
  -scan) inside KnowsAboutYou (404 = today's card); the bell maps
  `via == "scan"` only, `quiet` (no badge, no desktop toast). Pins:
  `tests/test_corrections_v1305.py`, `tests/test_preferences_v1305.py`,
  `dashboard/__tests__/preferences-v1305.test.tsx`.
  v1.305.1 (test-only): v1.305.0's Release gate went red on
  `continue-next-v1303` F4 — `findByTestId` found the copy sentence while it
  still read "the next account" (before the light snapshot landed) and
  asserted the NAMED one; the wait is now on the named sentence itself.
  Reproduced locally by delaying the mocked read 150 ms (old shape: CI's exact
  error; new: green).

- **The profile reaches a Build pane ONLY through a switched-on, generated
  block in the CLI's own instructions file — outside text byte-identical,
  a hand edit never overwritten** (v1.306.0; idea from agent-personalizer,
  MIT — no code taken). VERIFIED on this PC, not guessed: Claude Code 2.1.290
  reads `<CLAUDE_CONFIG_DIR or ~/.claude>/CLAUDE.md` as its "User"
  instructions (binary: `case"User":return Ve(we(),"CLAUDE.md")`, `we =
  CLAUDE_CONFIG_DIR ?? homedir/.claude`; LIVE with a throwaway
  CLAUDE_CONFIG_DIR and a throwaway USERPROFILE — the session record carried
  the file as an `instructions` attachment even with no login, and the
  `<!-- -->` marker lines were dropped from what the model reads); Codex
  0.157.0 reads `<CODEX_HOME or ~/.codex>/AGENTS.override.md` when it holds
  non-whitespace, else `AGENTS.md` (`codex debug prompt-input` with a
  throwaway CODEX_HOME — no model call; Codex ignores a USERPROFILE override
  for its default home, so `~/.codex` is from the binary/docs). The
  user's real `~/.codex/AGENTS.md` exists and is EMPTY — a file we did not
  create is never deleted. `profile/share.py` (`ProfileShare`, built in
  `build_platform`, `platform.profile_share`): content = `LEAD` +
  `profile.render` + `# Preferences they approved` from
  `confirmed_preferences` (`source == "preference"`, user scope,
  `confirmed_clause()` — feedback/distilled/reflection/project rows never;
  the v1.305.0 raw-select grep pin lists `profile/share.py`), each line
  `promptguard.scan_context` (a flagged preference DROPPED, not
  placeholder'd), `redact.mask`, `scrub_paths`, ≤ 280 per line,
  `MAX_SHARE_CHARS` 4,000 (`omitted` counted), marker words neutralised and `<!--`/`-->` broken up (the CLI drops
  HTML comments, so a typed opener would hide the rest); an
  empty render writes nothing. The REGION is the start marker through the
  end marker plus ONE newline, inserted right after any BOM at the top,
  replaced in place, or cut out — so outside bytes are identical after
  write, rewrite and removal (CRLF/BOM/no-trailing-newline/empty fixtures);
  newline style follows the file; a non-UTF-8 file, damaged markers, or a
  SYMLINKED / HARD-LINKED file are left alone (`os.replace` would turn the
  user's dotfiles link into a plain file — `LINK_ERROR` on the row). State `<IJ home>/profile-share.json` per file: sha256 of the
  normalised region (`hash`), `created`, `backed_up`, `held`, `written_at`.
  DRIFT = region differs from `hash`, or the block/file we wrote is gone →
  never written until `POST /profile/share/{cli}/overwrite {path?}`;
  `POST /profile/share/{cli}/keep {path?}` sets `held` (frozen until
  Overwrite, even if the text matches again). First write to an existing
  file copies it to `<IJ home>/trash/<stamp>/profile-share/<drive>/<path>`
  (once per file); writes are temp + `os.replace`, and the file is read
  AGAIN right before the replace (`_atomic_write(expect=)`, `FileChanged`):
  a save that landed after Jarvis's read wins and the write is abandoned
  (the window left is the re-read-to-replace gap, not the whole write).
  Off removes every tracked block (an edited one copied to trash first) and
  deletes a file WE created that is blank after; a file whose block could
  not come out (locked, a link, damaged markers) stays listed and the row
  still says so with the switch off. HOMES: the default (env var else
  `~/.<cli>`) + every Iron-Proxy CLI account `cli.home` for that provider
  from the CACHED snapshot (never a call); `is_default_home` / same path =
  one file; Iron-Proxy OFF = default only (UNEDITED account blocks taken back);
  Iron-Proxy on but unread (`cached_accounts() is None`) = account files
  left alone. `IronProxyService.add_accounts_listener` fires on
  `set_enabled` and when the snapshot's account-home set changes. TRIGGERS:
  `notify_changed()` after the commit in `ProfileStore.save`,
  `preferences.keep/edit/forget`, `LearningEngine.note_preference`,
  `POST/DELETE /lessons`, the memory graph's lesson delete
  (`POST /memory/graph/node/delete`), plus a boot `poke()` in the lifespan
  — `poke` only arms a `DEBOUNCE_S` (1.5 s) `threading.Timer` named
  `profile-share`, so nothing runs on the loop and boot never waits; at
  shutdown `close()` cancels a pending timer and the lifespan waits (off
  the loop, ≤ 5 s) for a write already running.
  SETTING: `Config.profile_share_claude_code` / `profile_share_codex`,
  persisted by `PUT /profile/share` ONLY (not in `_SETTINGS_KEYS`, the
  `iron_proxy_enabled` reason: a settings write would flip the flag
  without writing or removing anything). Routes (`routes/profile_share.py`,
  all file work in `to_thread`): `GET /profile/share` → `{clis: [{cli,
  label, vendor, available, on, file_name, files: [{path, account, exists,
  last_written, drift, held, created, error}], targets: [{path, account}],
  last_written, drift, accounts_known, chars, omitted}], limit}`; `PUT
  {cli, on}` (404 unknown cli, 409 not installed) → the view after the
  write/removal. DASHBOARD: `components/memory/ProfileShareRow.tsx` at the
  foot of KnowsAboutYou ("Share with Build"; `#profile-share-<cli>`; a CLI
  not found = no switch; 404/failed read = no row), words in
  `lib/profileShare.ts` (`shareSentence` names the files and the vendor
  who sees it; `fileLine` for edited/removed/broken/held/error). Pins:
  `tests/test_profile_share_v1306.py` (real temp homes via
  USERPROFILE/HOME; a teardown check that the REAL files are untouched),
  `dashboard/__tests__/profile-share-v1306.test.tsx`.

- **A user reads WORDS, never ids; an empty page shows the way; the demo
  strip is subtracted from every full-height module** (v1.314.0, /goal UX &
  aesthetic wave 2 — 31 findings + 4 wave-1 carry-overs). ONE place per
  vocabulary, import never fork: `lib/onboarding.providerDisplay(id)` is the
  provider word anywhere a user reads one ("mock" → "Demo model (scripted)",
  "auto" → "Auto"; the VALUE posted stays the id, a record-of-what-ran row
  keeps the raw id in `title`); `OriginChip.originLabel(origin)` reads origins
  in words ("Schedule · nightly-brief", "Mission", "Queued job" — an id
  suffix is never shown, an UNKNOWN origin is shown raw, never guessed; raw in
  `title` + `data-origin`); `lib/toolWords.ts` says what a BUILT-IN tool does
  on the approval card ("save a file") — built-ins only, keyed by exact name
  (an agent-made `browser_backup` is not a browser tool), unknown → "run
  <id>", the exact id stays on the card because a grant is keyed on it;
  `lib/theme.ts` is THE theme store (list + `applyTheme`) behind the bar dots,
  the phone drawer, Settings → Appearance and the palette's "Theme: <name>"
  commands. `MockChip` reads "demo model". `<Empty title action={{label,
  href} | {label, onClick, disabled}} secondary examples>` — an onClick
  action opens the page's OWN form (never a second copy) and mirrors its busy
  flag. Updates tells a FAILED check (`reason` "git error: …") from real
  local changes. HEIGHT: `SimulatedBanner` publishes its rendered height as
  `--ij-strip-h` (ResizeObserver; 0px when hidden or unmounted) and every
  `h-[calc(100vh-…)]` module subtracts `var(--ij-strip-h,0px)` (chat, the
  agents team screen + its loader, the workflow canvas, Build's rail) — the
  strip pushed the chat composer's footer below a 1440x900 window; a new
  full-height module must do the same (the coordinator pin fails on a bare
  `h-[calc(100vh-Xrem)]` in those files). Handbook: "What changed in the look
  and feel". Pins: `dashboard/__tests__/ux-wave2-{contracts,chat-words,
  work-surfaces*,automation,system-memory,coordinator}-v1314.test.tsx`,
  `tests/test_ux_wave2_templates_v1314.py`. Carry-overs to later waves:
  ApprovalCard's `startsWith("browser_")` tab answer → the toolWords list;
  desktop/main.js ask watcher's own "rename a file" copy; reflex/sentinels
  empty-state Add should scroll to the form like Schedules.

- **The shell never claims a model during a demo; colour lives in TONE
  tokens; a phone page never pans sideways** (v1.313.0, /goal UX & aesthetic
  wave 1 — 51 findings from a screenshot audit of every view: fresh install,
  lived-in desk, whole page, phone, Daylight). SHELL: `lib/onboarding.
  noModelChosen` is THE demo rule (ModelSwitcher, SimulatedBanner and the
  Overview import it): while replies are scripted the chip says "Demo
  replies" (lg+) / an amber dot (every width) and NEVER a model id; the demo
  strip also shows when a model is connected but none chosen, and its
  "Choose a model" opens the model menu in place. The phone title bar has no
  overlapping control (36 px controls below sm, icon search, brand dot);
  the theme row lives in the nav drawer on a phone; theme dots use a deeper
  `onLight` shade on light themes (≥ 3:1); the mood orb rests as dot+ring.
  Crumbs = the page's own title: `lib/nav.CRUMB_LABELS` (NOT nav rows —
  nav.test pins the sidebar) covers /kanban, /marketplace ("Directory"),
  /ltm + /lessons ("Memory") and /fleet ("Fleet"); sidebar words are
  "Self-development" and "Train Jarvis on me", the old words kept as
  aliases. DESIGN SYSTEM: `--tone-success|danger|warn|info|violet` RGB vars
  (dark = the old -300 tints, mark1/mark8 = deep inks), exposed only as
  `tone-*`; a GENERATED block between `/* light-tone-overrides:start|end */`
  in globals.css re-inks every pale hue utility the code uses on the light
  themes — `uv run python scripts/gen_light_tones.py` regenerates it and the
  coverage test fails on any new pale class without a rule, so RE-RUN IT
  after adding one (sort key ends on the class name, so a re-run is
  byte-stable). `ui.tsx` adds `<Code>`/`.code-inline` (theme-true code
  chips — never `bg-black/NN`) and `<Button>`; `<Modal>` traps and restores
  focus and takes a `z` prop; reduced motion is honoured in MotionProvider.
  FIRST RUN: Overview hero says "Checking in…" until /health answers (only
  under the real DaemonProvider), the model card keeps a quiet "Other ways to
  connect →" link, HealthCard speaks plainly ("Tasks reviewed", "Finished",
  "Tools worked", "Typical reply time" — the metric's raw name in `title`),
  and a door's count is a "10 modules" caption, never a corner bubble that
  reads as unread. PHONE: `components/PageGrid.tsx` is the one page grid
  (`grid-cols-[minmax(0,1fr)]` below lg + `[&>*]:min-w-0`); a leading icon
  over a `.field` is `z-[1]` in an `isolate` wrapper (it painted UNDER the
  field); PageHeader's always-rendered hint popover is capped
  `max-w-[min(28rem,calc(100vw-2rem))]` — at `max-w-md` its invisible box
  widened <main> to 460 px and most pages panned 70 px sideways on a phone.
  Carry-overs (later waves): raw `grid lg:grid-cols-3` on templates/updates/
  self-dev/autonomy/channels/computeruse → PageGrid; palette "Theme:"
  commands + a Settings Appearance row; EmailComposeDialog/LongTerm/Creative
  lightbox/FilesPanel onto `<Modal>`; the model menu panel opaque. Pins:
  `dashboard/__tests__/ux-wave1-{shell,design,firstrun,phone,phone-pages,
  phone-canvas,coordinator}-v1313.test.tsx`.

- **When something goes wrong, the user sees what happened and has a way
  forward** (v1.312.0, /goal adoption wave 4 — the last 4 findings).
  PREPARATION IS VISIBLE AND STOPPABLE: `/chat/stream` answers at once after
  `routes/chat._eager_checks` (400 empty / 404 unknown skill stay status
  codes); grounding, attachments, compaction and tool choice run INSIDE the
  stream, each raced against the named turn's Stop (`_prep_step`), with
  additive `event: phase` frames (`recalling` | `reading_files` only when the
  turn carries files | `summarizing` only when a compaction runs |
  `choosing_tools`); a Stop in preparation ends the turn exactly like a Stop
  today, with no model call and nothing billed; a preparation fault is ONE
  plain error frame. `useChatStream` decodes `phase` in both reducers and the
  bubble says it in words; the page's Stop also POSTs
  `/chat/turns/{id}/stop`; the old prep bound stays for a daemon that sends
  nothing. A GRANT IS NOT AN ARMING: `ChatBody.granted_tools` (uncapped,
  persisted in thread setup — `_clean_setup` keeps it) is the conversation's
  "Allow for this conversation" list; BOTH lanes add a granted name to
  `armed_grant`/`card_grants` (`chat_turn._conversation_grants`) ONLY when the
  turn armed it some other way — it never reaches `armed`/`ask_armed`/
  `allowed_names` (AST-pinned); the page still arms when the cap has room and
  SAYS so when it does not. RETRY: during a provider cooldown Retry reads
  "Retry in Ns" and is disabled; when the effective provider is known down a
  "Choose another model…" button opens the picker — the page suggests nothing
  and never switches. DESKTOP: `shellState().services.daemon` carries
  `{capped, restarts, lastExit, damaged, restarting, stalled}`;
  `shell:restartDaemon` (sender-checked) respawns ONLY the daemon, never the
  dashboard the user is looking at; `DaemonBanner` tells restarting / capped /
  stalled apart with Restart + Open logs, and points at the tray's Restart,
  not Quit. `stalled` belongs to the CHILD the watchdog gave up on —
  `startService` clears it on any respawn. v1.311.1 (test-only, the same
  day): the Release gate went red on `preferences-v1305` — the test pressed
  Forget while the section's busy guard was still up (it waited for the PATCH
  to be recorded, not for the edit to finish); reproduced by delaying the
  mocked PATCH. v1.311.1's own Release gate then went red on
  `test_preferences_v1305` — the look-back's `TURN_BUDGET_S` (2 s) is an
  absolute wall-clock bound, and on the loaded runner a CORRECT look-back
  missed it (`suggestion: None`); the file now sets a generous budget in an
  autouse fixture (the budget test still sets its own tiny one), reproduced by
  starving the budget. Pins: `tests/test_wave4_chat_recovery_v1312.py`,
  `test_wave4_prep_race_v1312.py`, `test_chat_setup_granted_tools_v1312.py`,
  `test_desktop_offline_banner_v1312.py`,
  `dashboard/__tests__/{chat-recovery,chat-recovery-phase,chat-recovery-followups,daemon-banner}-v1312.test.tsx`.

- **Fast where people wait: the turn, the boot, the poll** (v1.311.0, /goal
  adoption wave 3 — 17 speed findings). CHAT LANES (both, lock-step):
  `chat_turn._gather_grounding` runs every independent grounding hop
  CONCURRENTLY off the loop and the lanes join the result in the old fixed
  order (byte-identical prompt, pinned; learning runs on "" because it
  APPENDS; attachments/skill/workspace stay outside; no silent deadline);
  connector toggles are resolved ON the loop (a worker iterating the registry
  raced a pack registering). Auto-compaction finally runs
  (`_compaction_complete` read off `platform` first). A round's tool calls
  go through `_run_tool_round`: every card is answered first, then only the
  allow-list `CONCURRENT_READ_TOOLS` (never browser/desktop/pane tools, never
  a reader that cards, never `_store_as`) runs with `asyncio.gather`, its
  taint applied in call order BEFORE the writers run serially in model order;
  frames and `role=tool` messages keep the original call order. The stream
  lane STREAMS the final-answer nudge and the language rewrite (a new `reset`
  frame clears the bubble; `useChatStream` handles it in both reducers;
  `done.reply` stays authoritative). `GET /chat/threads` is ORDER BY/LIMIT in
  SQL with a `json_valid`-guarded count (`ix_chatthreadrecord_updated_at`).
  `estimate_tokens` is one run-class regex (~60x, byte-identical);
  `fabric.recall` fans its stores out per call, results in submission order.
  BOOT: the daemon builds the platform with `defer_mcp=True` and loads packs
  in the background (CLI callers of `build_platform` keep the synchronous
  contract); a pack reads `state: "starting"` (`/mcp/servers`, the Tools
  badge, the doctor) until it answers; `platform.mcp_ready` is the lifespan's
  event and `mcp.tools.wait_for_starting_packs` makes a chat turn or an agent
  run wait up to `PACKS_TURN_WAIT_S` (10 s) for it, then `packs_starting_note`
  rides `_Grounding.after_skill()` (both lanes) and the runtime prompt —
  scoped to a platform whose OWN load is running (`_LOAD_STATUS` is
  process-wide); the assignment dispatcher waits on the same event; a Retry
  closes the clients of the tools it replaces. CLI presence is memoised with
  stale-while-revalidate (`cli_binary_present`, one background refresh per
  binary, generation-checked); `POST /providers/rescan` calls
  `invalidate_cli_presence()` first so Re-detect really looks again. The event
  bus runs sync handlers in ONE executor hop; outbound webhooks keep an
  in-memory subscription index (invalidated on any ORM write, one entry per
  (type, record)); undo/revert write off the loop; the Build Files panel keeps
  the newest N by mtime. DASHBOARD: `DaemonProvider` keeps /health when
  unchanged and `epoch` lives in `lib/daemonEpoch.ts`; `useApi`/`usePolledApi`
  share `useApiCore` (a poll never flips `loading`, an equal answer sets
  nothing, identical in-flight GETs are joined); `useEvents(n, {types})`
  filters per subscriber (Overview, bell, MoodOrb, notify bridge, downgrade
  banner, chat page). CHAT PAGE: reopening paints the cached thread at once
  WITH its setup and its PROJECT (`followThreadProject(cached)` — a null
  project on the paint untagged the thread), restores the last conversation,
  fetches the list once, and lazy-loads DocPreview/FilesPanel/DirectoryTree/
  EmailComposeDialog (`components/chat/emailDraft.ts` holds the helpers the
  page renders). Measured: /chat first load 283 → 266 kB. Pins:
  `tests/test_wave3_{chat_lanes,chat_helpers,platform_speed,packs_starting}_v1311.py`,
  `dashboard/__tests__/{hooks-wave3,chat-wave3,chat-wave3-reset,chat-wave3-seeded-race}-v1311.test.tsx`,
  `chat-wave3-deferred-v1311.test.ts`.

- **The first answer is REAL, the wizard tells the truth, and the first
  screen leads somewhere** (v1.310.0, /goal adoption wave 2 — 8 first-run
  findings + wave-1 carry-overs). THE MOCK TRAP: a user signed in to Claude
  Code/Codex (or running Ollama) is not first-run, so the wizard never opened
  while `default_provider` stayed "mock" and every first reply was the
  scripted mock. `POST /onboarding/use-model {provider}` (routes/settings.py)
  is THE one explicit press — wizard doors, the Overview card, the chat empty
  state all call it (`lib/onboarding.useModel` = `chooseForAnswers`); it
  promotes ONLY over the untouched mock default (else 200 `{promoted: null,
  reason}`), a CLI door with no stored key promotes the INHERITED API name (the
  dial keeps working) and a stored key never gets billed for a CLI press, an
  unknown/unavailable provider is a 409 sentence. Nothing promotes silently —
  not boot, not rescan. `GET /onboarding` carries `model {default_provider,
  default_model, is_mock, usable[{provider, label, local}]}` from CACHED
  availability (never `doctor(platform)` — integrity scan + probes), drops the
  dev-toolchain rows (`readiness.DEV_TOOLCHAIN_CHECKS`; the `ironjarvis doctor`
  CLI keeps them) and gives each row `level` + a plain `label`. /health's
  claude-cli/codex-cli rows carry `installed`, `signed_in` (null = unknown,
  never "signed out") and `sign_in_fix`. Checklist: step 1 is done only when a
  REAL model answers (Auto counts when something is connected), never "the
  offline model works"; "Teach it your style" counts confirmed non-reflection
  lessons or feedback only; `provider_label` names a keyless API name by its
  sign-in. WIZARD (FirstRunWizard + `components/onboarding/ConnectDoors`,
  `AnswerPress`): step 3 never runs on mock, names the model it asks, and
  celebrates by the provider that ACTUALLY answered (mock = amber "no model
  ran"); the subscription door has three honest states (not installed → the
  vendor page, signed out → a Build-pane sign-in with the CLI's own hint,
  signed in → the press); the chat empty state shows the doors while replies
  are a demo. OVERVIEW: `FirstRunStrip` sits under the header while
  `next_step != null` — welcome card, one "Ask Jarvis anything" box →
  `/chat?ask=` (suggest, never act), the Try-it-now cards — and is LATCHED for
  the visit so the press that ticks the last step keeps its "Done — X now
  answers your questions"; required failures inline, recommended ones folded
  into "Optional extras (n)". DESKTOP: Start-with-Windows is never asked on the
  first launch (a day later, parented to the shown main window, the flag
  written only when answered; a tray/menu toggle is an answer —
  `setStartAtLoginFromMenu`). CARRY-OVERS: `/chat/approvals/pending` rows carry
  `mission_id`/`project_id` (bell → "Open the mission"); a retry-failed run's
  system prompt gets `runtime.mission_retry_note` (keyed to the row's own id —
  a rerun or follow-up of a retry never inherits it; `_stamp_continuation`
  pops `retry_failed`); a teammate's model line follows its status; the retry
  409 is shown in plain words. Pins: `tests/test_wave2_onboarding_v1310.py`,
  `test_wave2_onboarding_review_v1310.py`, `test_wave2_followups_v1310.py`,
  `test_desktop_startwithwindows_v1310.py`,
  `dashboard/__tests__/{wizard-wave2,chat-connect-doors,overview-wave2,mission-followups}-v1310.test.tsx`.

- **A mission's teammates can ASK, can be STOPPED, are VISIBLE when remote,
  and a mission is never a dead end** (v1.309.0, /goal adoption wave 1 — 25
  verified findings from the 2026-10-07 theory audit). TEAMMATES: a child of
  a mission (`team.is_mission_origin` on the caller's ROOT,
  `team.mission_root`) is stamped `job:mission-member` (`MISSION_MEMBER_ORIGIN`,
  an attended origin), so its asks wait for the user under ITS OWN session id
  and the mission screen answers them inline; every other child keeps
  `origin=None` and the instant headless denial. `consult` checks the project
  team like delegate/spawn_agent. Stop on a teammate really ends it: the child
  runs as its own asyncio task (`team.bind_task`/`team.stop_child`), the
  runtime checks `_check_stop` each step, the coordinator is told and carries
  on. Remote delegations publish delegation events and appear as members.
  `session.completed` carries `origin`, `project_id` and `outcome`; the bell
  maps a mission's completion ("Your objective is done" / failed / needs you —
  never "done" for a failure) to `missionLinks.sessionHref`. VIEW:
  `mission_view` gains `objective` (the user's words; a continuation stores
  them as `options.objective`, a restart Continue sends `ContinueBody.resume`
  so the restart note never becomes the objective), `interrupted`,
  `continued_as`, `route_note`; members carry `provider`/`model`; `GET
  /missions` rows carry `objective` + `waiting` ("Needs you", never "Working",
  for a mission parked on an ask). The 2 s poll reads the ledger in ONE
  transaction per view. `POST /missions/{id}/retry-failed` resets the failed
  worklist items AND starts the continuation in one server step, in the
  mission's own folder (409 when nothing failed or a follow-up already runs);
  a retry of a retry names the ORIGINAL objective once. `/sessions/interrupted`
  lists the mission ROOT only, never its teammates. DASHBOARD: the result
  panel shows a teammate's draft, never the coordinator's (or a member's)
  narration before a tool call; the live report re-parses only what changed
  (lock-step with `components/chat` streaming markdown); a token flush does
  not re-render the cards; the receipt names the model in the run's own tense
  (tried / ran on / working on / answered by) and flags mock amber; the door
  warns before Start when the default is down (and says "could not check" on a
  stale /health); a finished mission offers Ask for changes / Run it again
  (a FAILED one re-posts through `POST /missions` on the current default) /
  Retry the N failed items; `/agents?view=team&agent=<name>` opens that
  agent, and every bell/palette agent link uses it; TeamScreen and
  RoomTranscript load only when opened. ONE definition of each origin string:
  `agents/team.py`. Pins: `tests/test_wave1_teammates_v1309.py`,
  `tests/test_wave1_mission_view_v1309.py`,
  `tests/test_wave1_guide_words_v1309.py`,
  `dashboard/__tests__/{mission-wave1,links-wave1}-v1309.test.tsx`,
  `dashboard/__tests__/mission-live-markdown-lockstep-v1309.test.ts`.

- **The round table is GONE from the dashboard; missions are the only way
  team work is given, and a project's TEAM is enforced** (v1.308.0). The
  user, seeing v1.307.0: "the roundtable view I saw wasn't very user
  friendly … this new method would be preferred" — and chose "replace it
  everywhere". `app/agents/page.tsx` is a small router over FOUR screens
  (`lib/mission.parseAgentsRoute`/`agentsPath`/`missionPath(id, project)`):
  mission (`/agents`, `?mission=`, and a PROJECT's screen
  `?project=<pid>[&mission=]` — header + back, `MissionComposer
  fixedProject`, `ProjectWork` = Board / Waiting on you / Completed reusing
  `world/WorldBoard|WaitingList|CompletedList` off `GET /projects/{id}/world`,
  `ProjectTeamPanel` + `TeamEditor` in a Modal; the front door lists
  `ProjectTeams` off `GET /agents/worlds`), team (`?view=team`, and the old
  `?world=general` → `TeamScreen` = rail + `AgentsPanel`, the agents dialog's
  body lifted out of `AgentsModal`, which now just wraps it; Talk / Give work
  are not passed), room (`?thread=` → `RoomTranscript`, READ-ONLY, wins over
  `?project=`), guide (`?talk=&ask=` → `guideChatPath` = `/chat?ask=@guide
  …`; Help's Ask the Guide links there directly). DELETED: RoundTable,
  ThreadRail, PanelPicker, JobPostCard, FaceStack, world/WorldView,
  world/WorldTabs, world/NewTaskPanel, `AgentSeatModal`, the world route
  helpers in `lib/agentWorlds` — and the 17 test files that only covered
  them (mixed files lost only their round-table describes). KEPT: the backend
  rooms (`agents/threads.py`, `/agents/threads*` — chat @-mentions, the phone
  and remote inbound still use them), `TableSeats`/`RosterStrip` helpers.
  THE TEAM IS REAL: `POST /missions` with a project snapshots
  `Project.team_json` into run options `team` (none = unrestricted);
  `agents/team.mission_team/on_team/off_team_refusal`; the runtime's roster
  block is `roster_block(only=team)`; `delegate` AND `spawn_agent` refuse an
  off-team target before any session exists (read off the CALLER's row via
  `team.mission_root`/`team.team_for_session` since v1.309.0, which also
  covers `consult`). `GET /missions?project_id=`. A remote on
  the team "sees only the task Jarvis hands it" (`projects/world.REMOTE_SEES`
  + `lib/agentWorlds.REMOTE_SEES`, lock-step wording). Pins:
  `tests/test_agents_project_missions_v1308.py`,
  `dashboard/__tests__/agents-mission-v1307.test.tsx` (v1.308.0 blocks).

- **The Agents page is a MISSION screen: one objective, Jarvis runs the
  team, the deliverable first** (v1.307.0). The user's spec: "the user does
  not interact with each agent individually … one AI interface with a visible
  workforce behind it". BACKEND: `POST /missions {objective, project_id?,
  workspace_root?, provider?, model?, allow_tools?, approval_mode?,
  max_steps?}` → a SUPERVISOR session, `origin="job:mission"` (an ATTENDED
  origin: asks wait), run option `deliverable: true` → the runtime appends
  `MISSION_DELIVERABLE` (the final message IS the work product; only that
  door sets it; children never inherit options); `GET /missions` (newest 30
  by origin); `GET /sessions/{id}/mission` → `agents/mission.py::
  mission_view` (members from the `AgentRun.parent_id` walk + remote
  delegations from events; per-member status/`progress {pct, label,
  basis}`; a plain-words `activity` log from `ToolInvocation` (REDACTED
  args, basenames only, `_QUIET_TOOLS` skipped) + persisted delegation/plan/
  ask/completion events; `deliverable {text, documents, worklist}`). PROGRESS
  IS COUNTED OR ABSENT: done 100, queued 0, a plan's finished steps, else
  `pct: null` with words — never a clock-driven number (mutation-pinned).
  LIVE TEXT: `StreamHub.link/unlink/root_of` mirrors a delegated child's
  frames to its team ROOT as `{"event": "member", "data": {member, event,
  data}}` — never under the child's own name, so a member's `done` cannot end
  the root's SSE. `agents/team.py::working(...)` (a `with` block in BOTH
  `delegate` and `spawn_agent`) links the stream AND puts the child in a
  display-only live map the roster reads as busy — it grants no slot and no
  cancel handle (the roster's old blind spot, closed; `test_roster_truth_v1193`
  flipped with it). FIXES from the agents review ride along:
  `supervisor.with_worklist` carried no `skills`/`reports_to` (a custom agent
  on a bulk task, and every scheduled run with skills, lost both);
  `Orchestrator.settle_child` is the ONE success-path settle for delegated/
  spawned children (worklist claims released, `outcome` derived, folder note
  kept, claimed-write note) — both doors used to copy fields by hand.
  DASHBOARD: `/agents` = `MissionScreen` (left `MissionRail` — page-local,
  the app drawer is untouched — centre `MissionOutput` with Report | Markdown
  | Preview + inline approval + Stop, right `AgentCards` (compact, expand in
  place, `ProgressBar` draws a number ONLY for a non-null pct), `LiveActivity`
  below spanning centre+right); `?mission=<id>` one objective; the team
  screens moved to `?view=team` (`lib/mission.agentsScreen`; every old key —
  project/thread/world/talk/ask — still opens them, and `worldPath({kind:
  "auto"})` is `/agents?view=team`); `lib/useMission.ts` polls the view every
  2 s while running and reads the coordinator's ONE EventSource (member
  frames included), flushing tokens to state every 120 ms. `/agents` joined
  Simple mode's nav. Pins: `tests/test_agents_mission_v1307.py`,
  `dashboard/__tests__/agents-mission-v1307.test.tsx` (the seven older
  agents-page files now set `?view=team`).

- **An agent's run ends honestly** (v1.288.0, deep review wave 3). (1) Shell
  and custom-tool output is captured as BYTES and decoded ONLY by
  `sandbox/native._as_text`: strict UTF-8, else the OEM or ANSI page, chosen by
  WORD SHAPE (`_plausibility`) with `errors="replace"`. Never put `text=True`
  back on a subprocess whose output reaches the model — one undecodable byte
  kills `communicate()`'s reader thread and returns `''` with rc 0, an EMPTY
  success. The page cannot be fixed at the source: measured, `dir` into a pipe
  writes OEM whatever `chcp` says, and `chcp` in a child flips the DAEMON's
  shared console. Counting accented letters is not enough (ANSI curly
  quotes/dashes 0x91-0x97 ARE OEM letters "æÆôöòûù"; OEM "ö" is ANSI's ”) —
  position decides (review: a box-drawing run with a letter at BOTH ends is
  a word read wrong, "N┌╤EZ"; a capital accent inside a lower-case word is
  a hump, "JosÚ" — OEM 850's reading of ANSI é/í/ç/ë; 850's Ð/Ý/Þ/þ/Ù/Ë
  weigh half). Measured on BOTH pairs (437+1252, 850+1252) — the numbers
  and the known losses are in `_decode_fallback`'s docstring; the 850
  all-caps ties (ANSI "BJÖRN" vs OEM "GARCÍA" are the same bytes) are
  documented, not forced. Children get `stdin=DEVNULL` (the packaged daemon's stdin is a pipe
  Electron never writes). ARGV children only (`run_code`, `custom:*`) get
  `native.child_env` (PYTHONIOENCODING=utf-8); the `shell` child does NOT — a
  shell line pipes and redirects, and a forced UTF-8 breaks `type x.csv |
  python` on an ANSI byte (pinned). A stdin pin must not use `communicate()`
  (it closes stdin → EOF anyway).
  (2) A tool that awaits a whole sub-agent run sets `Tool.deadline_exempt`;
  tell a deadline from a user Cancel with `registry.tool_deadline_expired()`,
  never "the parent is still ACTIVE". (3) A delegated/spawned child inherits
  the parent's stored `allow_tools` + normalised `approval_mode`
  (`orchestrator.inherited_grants`), NEVER its origin — so a worker still
  cannot pause to ASK (its card would route under its own session id, which
  the parent's chat page does not render). ONE EXCEPTION since v1.309.0: a
  MISSION's teammate is stamped `job:mission-member` (attended) and asks,
  because the mission screen renders asks under each member's own id.
  (4) Every terminal path (`_finalize_cancelled`, `_finalize_failed`, the
  phone's `_run_dynamic_session`, boot reconcile) settles the session's
  AgentRun rows — the finalizers after draining in-flight WAITING→RUNNING
  restores — and the boot reconcile also repairs rows left RUNNING/WAITING
  under an already-terminal session (the ghosts older finalizers made). The
  `delegation.completed` event names the time limit too, not "cancelled". (5) httpx `timeout=` is PER
  PHASE; a remote call that must end within N s wraps `asyncio.timeout(N) as
  cm` and only `cm.expired()` earns the deadline wording. (6) An empty final
  message after tool steps becomes `runtime._no_final_text_result` (which tools
  ran, the last one's output through `_user_facing_tool_output`: the breaker's
  repeat note and refusal and the untrusted fence are the loop's words to the
  MODEL, never shown; a head-cut says so) — no extra model call. A remote's
  `timeout_s` is clamped >= 1 on create as on PATCH (it is a total deadline).
  Pins: `tests/test_shell_output_encoding_v1288.py`,
  `test_subagent_deadline_v1288.py`, `test_team_child_grants_v1288.py`,
  `test_failed_run_rows_settle_v1288.py`, `test_remote_deadline_v1288.py`,
  `test_agent_empty_final_v1288.py`, `test_wave3_leftovers_v1288.py`.

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
