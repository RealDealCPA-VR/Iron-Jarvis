# Build's App view: drive the real Claude Code / Codex from a GUI (v1.409.0)

The user, 2026-10-29, beside a screenshot of Chat's Code view and one of Build:
"in the build module [have] a TUI … to control any harness they desire …
similar to the code, but it would run and control the claude harness or the
codex harness … like https://github.com/pingdotgg/t3code … only effect the
build module." Approved as planned: Claude Code and Codex first; the harness's
own approvals answered by Iron Jarvis cards; a **</> Code | Terminal** switch at
the top of Build (Code is this view); Open in terminal both ways; Pi next, then
OpenCode.

## What is different from Chat's Code view

Code view: Iron Jarvis runs every tool; Claude/Codex only think
(`claude_native` / `codex_native` switch the harness's own tools OFF).

App view: the HARNESS runs everything itself, exactly as in a terminal: its
own tools, settings, CLAUDE.md / AGENTS.md, MCP servers, slash commands,
hooks, permission rules, its own session file. Iron Jarvis only starts it,
draws what it does, relays the user's messages and answers its permission
questions. Nothing from `claude_native` / `codex_native` that switches
built-ins off may be reused for this; reuse only process plumbing.

## Processes

- One long-lived harness process per OPEN App session, started through
  `core/procs.spawn` (kill-on-close Job, tree kill), env built exactly like a
  Build terminal's (`terminals.manager._with_pane_env` rules: the parent
  Claude Code session markers dropped, the chosen Iron-Proxy account's home
  var applied with `iron_proxy.accounts.Lease.apply`, an adopted default home
  sets no var). The user's own ANTHROPIC_/OPENAI_ keys are NOT stripped (a
  terminal would see them too) — same rule as a pane.
- cwd = the session's folder (validated by `fs_policy.root_problem`).
- Idle reaper: a process with no turn for 15 min is stopped; the next turn
  starts it again resuming the SAME harness session. At most 6 live; the
  oldest idle one is stopped first. Archive / delete / daemon shutdown stop it
  (tree kill, off the loop). Nothing blocks the event loop: every read is on a
  reader thread, every write via `asyncio.to_thread`.
- Claude Code (2.1.311 here): `claude -p --input-format stream-json
  --output-format stream-json --verbose --include-partial-messages
  --permission-prompts host --permission-mode <mode> [--model m] [--effort e]
  (--session-id <uuid> | --resume <id>)`. Permission questions arrive as
  `control_request` subtype `can_use_tool` and are answered with a
  `control_response` (allow + updatedInput / deny + message). Interrupt =
  `control_request` subtype `interrupt`; live mode change =
  `set_permission_mode`; live model change = `set_model` (measure what 2.1.311
  accepts; refuse in words what it does not). AskUserQuestion arrives as a
  can_use_tool for that tool: answer by allowing with updatedInput carrying the
  answers (read Claude Code's own shape; verify live).
  The session id is recorded so `claude --resume <id>` in a terminal opens the
  same conversation.
- Codex (0.157.0 here): `codex app-server` (stdio JSON-RPC), NOT ephemeral,
  NONE of `codex_native.protocol.BUILTINS_OFF`. thread/start (or
  thread/resume) with cwd, the approval policy and sandbox from the chosen
  mode; turn/start per message; turn/interrupt. Approval requests
  (`item/commandExecution/requestApproval`, `item/fileChange/requestApproval`,
  the legacy `execCommandApproval` / `applyPatchApproval`, and any other
  request whose shape the schema shows — read
  `codex app-server generate-json-schema`) become cards. MEASURE on this PC
  what `workspace-write` does on Windows (`codex exec --sandbox
  workspace-write` refused every command here in v1.363.0); if it cannot run
  commands, the "Ask first" preset must still let a command run after the
  user allows it — say in the code which sandbox each preset uses and why.
  The thread id is recorded so `codex resume <id>` opens the same thread.

## Modes (the composer's permission chip)

| Preset | Claude Code | Codex |
|---|---|---|
| Ask first (default) | `default`/`manual` (whatever 2.1.311 calls it) | approvalPolicy on-request (or untrusted) + the sandbox that works |
| Auto edits | `acceptEdits` | on-request + workspace-write if it works |
| Plan | `plan` | (none — hidden for Codex) |
| Full access | `bypassPermissions` | never + danger-full-access |

Full access takes TWO presses in the UI and the daemon refuses it unless the
request says `confirm: true`. Nothing is granted by Iron Jarvis on its own:
every permission question goes to the user (no auto-allow, no grants reused
from chat). "Allow for this session" maps to the harness's own session-wide
allow when it offers one (Claude's `updatedPermissions` suggestions; Codex's
`acceptForSession`), else is not shown.

## Daemon API (`daemon/routes/build_app.py`, package `iron_jarvis/hosted/`)

Store: `HostedSessionRecord` (late model, `_LATE_MODEL_MODULES`): id, harness
(`claude-code` | `codex`), folder, title, account (Iron-Proxy profile id or
""), model, effort, mode, harness_session_id, created_at, updated_at,
archived_at, last_error. Events: `<home>/hosted/<id>/events.jsonl`, the
normalized events below, append-only, bounded (keep the newest 20,000 lines /
20 MB; say when older ones were dropped).

- `GET /build/app/harnesses` → `[{id, label, installed, signed_in (null =
  unknown, cached verdicts only — never spawn a probe), version, modes:
  [{id, label, dangerous}], models: [{id, label}], efforts: [...],
  accounts: [{id, name, status}] }]` (accounts from the CACHED Iron-Proxy
  snapshot, like terminals).
- `POST /build/app/sessions {harness, folder, account?, model?, effort?, mode?}`
  → 201 session (process not started until the first message).
- `GET /build/app/sessions` (list, newest first, archived hidden unless
  `?archived=1`), `GET /build/app/sessions/{id}` → `{session, events, running,
  pending}` (pending = open approvals/questions),
  `PATCH /build/app/sessions/{id}` (title, mode (+confirm), model, effort —
  partial; applied live when the process runs), `POST …/archive`,
  `DELETE …` (refused while a turn runs).
- `POST /build/app/sessions/{id}/turn {text, turn_id, files?}` → SSE of the
  events below. The turn is the DAEMON's (`durable_turns.TurnLogs` pattern:
  closing the page never stops it); `GET …/turns/{turn_id}/stream` rejoins.
  A second turn while one runs: Claude takes it as a queued user message
  (stream-json accepts more input) — or refuse 409 in words; choose and say.
- `POST /build/app/sessions/{id}/answer {request_id, decision: allow|
  allow_session|deny, message?, answers?}` (404 unknown/answered).
- `POST /build/app/sessions/{id}/interrupt`.
- `POST /build/app/sessions/{id}/open-terminal` → a Build terminal (the
  `/terminals/launch` machinery, same folder, same account) typing
  `claude --resume <id>` / `codex resume <id>`; first stops the App view's
  process (two processes on one session corrupt it); refused while a turn
  runs. Answer `{terminal_id}`.
- `POST /build/app/from-terminal/{pane_id}` → an App session for the
  conversation a terminal recorded (`claude --session-id` on the pane,
  `resume_cli`/session facts the terminal code already keeps); refused in
  words unless the pane's harness has exited (pane at its shell prompt).

## Normalized events (THE CONTRACT between daemon and dashboard)

Every event: `{type, at, ...}`. Text fields are masked
(`detections.redact.mask`) before any cut.

- `session` {harness, harness_session_id, model, cwd, mode}
- `user` {text, files?}
- `text` {item, delta} · `text_done` {item, text}
- `thinking` {item, delta}
- `tool` {item, kind: read|edit|write|command|search|web|mcp|task|todo|other,
  name, title (one line, plain words), input (≤300), status:
  running|done|failed, output (≤1500), path?, line?, diff? (unified, ≤20,000)}
- `plan` {steps: [{text, status: pending|in_progress|completed}]}
- `approval` {request_id, kind: command|edit|tool|network|other, title,
  detail, command?, path?, diff?, can_session}
- `question` {request_id, questions: [{question, header?, options: [{label,
  description?}], multi}]}
- `resolved` {request_id, decision}
- `usage` {input, output, cache_read, cost_usd?, list_price}
- `notice` {text} (the harness said something that is not the answer:
  compacted, rate limit window, retrying)
- `error` {title, fix, technical} (`failure_words` shapes; sign-in →
  `fix_kind: "sign_in"`)
- `done` {stop: completed|interrupted|failed|max_turns, result?}

A golden fixture `dashboard/__tests__/fixtures/build-app-events-v1409.json`
is written FROM THE REAL DAEMON PARSER fed recorded harness output
(`IJ_REGEN_BUILD_APP=1`); the dashboard decodes the same file.

## Dashboard

- Build's header centre (`PageHeader center=`, the empty space) gets the
  switch **</> Code | Terminal** (`ij.build.view`, `?view=code`). Terminal is today's
  page byte-for-byte.
- App view: left list of App sessions grouped by folder ("New session":
  harness (by name, greyed with the reason when not installed / signed out),
  folder (the existing DirectoryTree picker), account, mode);
  centre conversation built from Chat's own pieces (Markdown, WorkLine-style
  step rows that open to their input/output, DiffView for edits with Open in
  editor, the plan checklist, DockAsk-style approval and question cards in the
  composer's place — Enter never allows a Full-access-only or once-only
  card); composer with chips: harness · model, effort, mode (Full access two
  presses), account; `/` lists the harness's own commands (Claude: the
  v1.379.0 command list + its built-ins it supports in -p; Codex: its
  prompts) and `@` files; Stop = interrupt; "Open in terminal" in ⋯.
- A Build terminal running claude/codex shows "Open in App view" when the
  CLI has exited and a session is recorded.
- The bell: an App session waiting on an approval with nobody looking → the
  same row/ping rules as a waiting chat (link `/terminals?view=code&code=<id>`).

## Not in this release

Pi — next; then OpenCode. Attempts / teams / night shift
stay in Chat's Code view. Phone answers for App approvals — later.

## Changes from the user (2026-10-29), and what the daemon measured

- The switch is **</> Code | Terminal** and a session links as
  `/terminals?view=code&code=<id>`; the routes keep `/build/app/…` (internal).
- Sessions are the tool's OWN saved sessions on disk: never ephemeral, always
  under the account's home, so `claude --resume <id>` / `codex resume <id>`
  keep working on sessions started here. `GET /build/app/sessions?folder=…&disk=1`
  lists the tool's existing saved sessions for that folder (`source: "disk"`,
  read-only, the `history/` reader's rules); `POST /build/app/sessions` with
  `harness_session_id` opens one in the Code view.
- Continue on the next account (Claude Code): an account limit in the stream
  (an assistant frame `error: "rate_limit"`, a `rate_limit_event` that is not
  `allowed`, the `terminals/limit_state` allow-list read on the harness's own
  words) is an `error` with `fix_kind: "account_limit"` and `account`;
  `POST /build/app/sessions/{id}/continue-on {account?}` copies the
  conversation with `continue_on.carry_over` into the next account's home and
  the next message resumes it there; refused in words when no other account
  can take over (and for Codex, for now).
- Next releases: Pi, then OpenCode (not ACP).
- MEASURED (claude 2.1.311): `--permission-prompts host` alone DENIES every
  tool that needs permission; `--permission-prompt-tool stdio` is also needed
  for `can_use_tool` control requests to arrive. Ask first = `default` (the
  flag and `set_permission_mode` both take it; `manual` is only a flag
  spelling).
- MEASURED (codex-cli 0.157.0, Windows): `read-only` and `workspace-write`
  cannot start a command (`CreateProcessAsUserW … Access is denied`), even
  after an allow. Ask first = `untrusted` + `danger-full-access` (asks before
  every command and every file change; an allowed one runs). Full access =
  `never` + `danger-full-access`. `on-request` + `danger-full-access` asks
  nothing, so Auto edits (and Plan) are not offered for Codex on Windows.
- A message sent while a turn runs is refused in words (409): Codex would not
  queue it and a queued Claude message would have no turn of its own to follow.

## Pi (v1.411.0) — what the daemon measured on pi 1.3.0

The user's staged plan: Claude Code + Codex first, then Pi, then OpenCode.
Pi is the third tool (`hosted/pi.py`); no new event kind was needed.

- Started as `pi --mode rpc --session-id <uuid> [--model provider/id]
  [--thinking level]` in the session's folder, through `core/procs` exactly
  like the other two (a Build terminal's env; a parent Pi session's
  `PI_SESSION_*` markers dropped). `--session-id` opens that project session
  and CREATES it when missing, so one argv starts and resumes; the id is
  minted before the start and recorded at once. Nothing that turns the
  user's own Pi down is ever passed (`--no-session`, `--no-tools`,
  `--no-extensions`, `--no-skills`, `--no-context-files`, `--offline` …).
- Protocol (JSON lines): a command `{id, type, …}` gets one `{id, type:
  "response", command, success, data|error}`; events carry no id. A
  `prompt` answers at once: `started` (a run follows; `agent_settled` ends it,
  `agent_end` does not), or `handled` (an extension command such as `/plan`;
  the turn ends after a short grace unless a run begins). Its answer may come
  only after its own dialog was answered, so it is never waited on. A prompt
  during a run is refused by Pi, and by the Code view (409, as for the other
  tools). `abort` → the message ends `aborted`, `agent_settled {aborted:
  true}`, then the abort's response.
- Text: `message_update` text/thinking deltas → `text` / `thinking`
  (`text_end` → `text_done`). Tools: `tool_execution_start/update/end` →
  `tool` rows (an edit's diff from its arguments, then Pi's own
  `details.patch`; a running command's output at most once a second).
  Usage: each assistant message's `usage` summed per turn; the cost is Pi's
  own price-table figure (`list_price: true`).
- Errors: a model that answers with an error ends its message `stopReason:
  "error"` (recorded: a gateway 400; "model not supported with a ChatGPT
  account"); a provider with no sign-in FAILS the prompt response ("No API
  key found for anthropic … Use /login"). Words are Pi's own: sign-in →
  "type /login in Pi", never another tool's Sign in or account press.
- Permissions: Pi asks nothing before a tool. ONE mode, `own` ("Pi's own
  rules"); Ask first, Auto edits, Plan and Full access are `hidden_modes`
  with the reason. Pi's plan mode is an extension (`--plan` / `/plan`) whose
  state it restores from the session and turns off by itself when the plan
  runs — so it is a `/` command, not a chip. Only extensions ask
  (`extension_ui_request`): `confirm` → approval (the running tool's card,
  `once_only`, answered `confirmed`); `select` / `input` / `editor` →
  question (`value`, or `cancelled` on Skip); `notify` → `notice`;
  `setStatus` / `setWidget` / `setTitle` / `set_editor_text` are not drawn.
  A dialog with `timeout` is answered by Pi itself; its card closes then.
  The user's own extensions on this PC gate no tool; plan mode asks "what
  next?" (`select`) and subagent asks before project-local agents
  (`confirm`).
- Sign-in and accounts: Pi signs in by itself, per model provider
  (`/login`, its own auth file). Iron-Proxy's accounts are Claude Code /
  Codex / Grok homes and do not apply: no account chip choice ("Pi's own
  sign-in"), `signed_in: null`, Continue on another account refused.
- Sessions: `<sessions dir>/--<folder>--/<timestamp>_<id>.jsonl` (header
  `{type: "session", id, cwd, timestamp}`), the sessions dir being
  `PI_CODING_AGENT_SESSION_DIR`, else the `sessionDir` setting, else
  `<PI_CODING_AGENT_DIR or ~/.pi/agent>/sessions`. The disk listing reads it
  read-only and matches ids from file names. In a terminal, `pi --session
  <id>` opens the same conversation (live-verified).
