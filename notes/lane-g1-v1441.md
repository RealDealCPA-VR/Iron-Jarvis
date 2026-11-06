# Lane g1 (v1.441.0): Build › Code session controls

## What changed, for the user

- **Claude Code's effort changes at once.** Picking another effort for a running Claude Code session now reaches it straight away (a quiet line says "Claude Code now works at high effort."). The effort chip stays open while a reply runs. If Claude Code does not take it live (an older Claude Code), it falls back to the old way: a restart before the next message, and the page says "from your next message".
- **Background work, with Stop.** The Session tab's Background section lists what the tool still runs in the background:
  - Claude Code: its open helpers and background commands;
  - Codex: its background terminals.
  Each row has a small ⋯ menu with **Stop**. Stop ends only that one; a running reply goes on. The conversation says what was stopped ("You stopped the background command “npm run dev”.").
- **Codex's background terminals keep the session alive.** The idle reaper and the six-session pool no longer stop a Codex session that still runs a command the model left in the background. This is the v1.418.0 rule Claude Code's tasks already had (two silent hours still end it).
- **MCP servers: one ⋯ menu per row, only what fits.**
  - Claude Code: **Reconnect**, **Turn off** and **Turn on** (Claude Code remembers "off" for this folder, as its own `/mcp` does).
  - Codex: **Sign in…** on a server waiting for a sign-in. It opens Codex's own sign-in page in the browser. Iron Jarvis never signs in. Codex starts the server again when the sign-in finishes, and the conversation says so.
  - The list re-reads at once after an action.
- **Older versions are left alone.** An older Iron Jarvis, or a tool without these features, shows the tab exactly as before: no menus, no list.

## What was measured, and how (no real model call, no sign-in)

### Claude Code 2.1.316

Measured in a scratch `CLAUDE_CONFIG_DIR` and `USERPROFILE`, with `-p --input-format/--output-format stream-json --permission-prompts host --permission-prompt-tool stdio`. The schema was read from the binary's embedded zod definitions.

- **`apply_flag_settings {settings:{effortLevel}}` changes the running effort.**
  - It answers `success`, and `get_settings` then reports `applied.effort` with the new level.
  - `max` and `xhigh` are taken. `null` goes back to the model's default (`medium` here, NOT the `--effort` the process started with). An unknown value is dropped silently, which is why the driver reads the effort back.
  - Proven against a stand-in Anthropic upstream (`ANTHROPIC_BASE_URL` = a local server, dummy key): the request after the change carried `output_config.effort: "high"`, the one before it `"medium"`.
- **`stop_task {task_id}` stops one background task.**
  - Set-up: the stand-in made the model start a background Bash (`ping -n 120`).
  - Result: an answer of `{}`, then `task_updated` (killed), `task_notification` (status `stopped`) and `background_tasks_changed` (`[]`). The ping process was gone, and no further model request came.
  - An unknown id also answers `{}`, so the driver checks the id itself first.
- **MCP control requests.** Measured with `--mcp-config` holding a working stdio server and one that exits:
  - `mcp_toggle {serverName, enabled}` answers `success`; `mcp_status` then shows `disabled`. It PERSISTS: `projects[<folder>].disabledMcpServers` in the config dir's `.claude.json`, cleared again by turning it back on.
  - `mcp_reconnect` of the failing server answers an error, "Connection closed".
  - `mcp_status` carries `error` for a failed server.
  - `mcp_authenticate` on a stdio server answers "Server type "stdio" does not support OAuth authentication". It is not used.
- **`background_tasks_changed {tasks:[{task_id, task_type, description}]}`** is a level signal. Only titles are taken from it.

### Codex 0.157.0

Measured in a scratch `CODEX_HOME`, with `codex app-server generate-json-schema --experimental` plus live runs.

- **`thread/backgroundTerminals/list|terminate|clean` (experimental).**
  - Set-up: a stand-in Responses server (`model_provider` in the scratch config) made the model call `exec_command` with `ping -n 120` and a 1.5 s yield.
  - After the turn, `list` returned `{data:[{itemId, processId:"87917", command, cwd, osPid:null,…}]}`. `terminate` returned `{terminated:true}`, and `list` was then empty.
  - An unknown id gives the error "invalid background terminal process id".
  - A terminal that ends by itself sends NO notification. That is why the driver re-reads every 30 s while any run.
  - `thread/shellCommand` does not create a background terminal.
- **`mcpServer/oauth/login {name, threadId}`.**
  - Set-up: a stand-in HTTP MCP server with OAuth discovery, dynamic registration, `/authorize` redirect and `/token`. `mcp_oauth_credentials_store = "file"` was set, so nothing reached the Windows Credential Manager (checked with `cmdkey /list`).
  - It answers `{authorizationUrl}` and listens on a loopback callback. Following the URL completed the flow.
  - It then sent `mcpServer/oauthLogin/completed {name, threadId, success:true}`, followed by `startupStatus` starting → ready in the same thread.
  - A stdio server is refused: "OAuth login is only supported for streamable HTTP servers."

Probes and stand-ins live in the scratchpad (`g1/probe_claude.py`, `probe2.py`, `standin.py`, `probe_codex.py`, `standin_resp.py`, `fake_oauth_mcp.py`). They were not committed.

## Pins

- `tests/test_session_controls_v14410.py` (30): the real drivers against a scripted peer answering the measured shapes:
  - effort live, back to default, and the three fallbacks;
  - task list, stop, no second line, an ended task;
  - MCP off/on/reconnect, then `mcp_status`, and the error kept;
  - Codex terminals, the mid-turn rule, the read after a turn, sign-in, unsafe addresses;
  - actions by state;
  - the overview only while running.
- `tests/test_session_controls_routes_v14410.py` (7): the real routes and service with the fake harness:
  - stop and its notice;
  - the not-running and unknown-session refusals;
  - Claude MCP actions with 400/404/409;
  - effort `now` with no restart, and the `next` fallback;
  - harness rows `effort_live`;
  - Codex terminals listed and stopped, the reaper keeping the session, and sign-in returning the URL.
- `dashboard/__tests__/session-controls-v1441-0.test.tsx` (10): the list and the ⋯ Stop (the list is read again after it), a refusal in words, an older daemon showing no list, MCP menus holding only the row's actions, Escape, sign-in through `window.open` with a fallback link, no unsafe address, an older daemon showing no menu, and `effortWhileRunning` with `effortLive`.

## Mutations: 21 of 22 red

- Red: M1–M16 (Python) and D1–D5 (dashboard).
- D6 survived: dropping CodePanes' `s.actions?.length` guard. This is equivalent: `RowMenu` itself renders nothing with no items, and the "no menu on a row with none" pins still hold.

## OPEN

- Claude Code's `mcp_authenticate` (its OAuth sign-in for an HTTP server) is not wired. Claude still signs in through "Open in terminal, /mcp".
- Codex has no Reconnect or Turn off here: the app-server has `config/mcpServer/reload` but no per-server toggle.
- Not driven end-to-end through the Iron Jarvis daemon with the real CLIs. The raw protocol was measured on the real CLIs, and the driver/route logic is pinned with doubles.
- Codex `thread/backgroundTerminals/clean` ("stop all") is measured (`{}`) but not offered.
- A background terminal's later output still reaches the conversation through Codex's `outputDelta` (unchanged behaviour).
- OpenCode, Pi, Grok and Antigravity offer neither list nor MCP actions (the driver hooks are generic, ready for them).

## Draft CLAUDE.md bullet

- **Build › Code's Session tab: background work with Stop, MCP actions, and live effort — the tool's own requests, offered only where the driver says so** (v1.441.0, lane g1).
  - `hosted/session_controls.py` is the one generic shape:
    - `Driver.background_work()` returns `[{id, title, kind helper|command|task, can_stop}]` or None (None means no list and no `background_work` key);
    - `stop_background(id)` returns the title;
    - `mcp_actions` (reconnect | off | on | sign_in) and `mcp_action(server, action)` return `{}` or `{url}`.
  - Routes: `POST /build/app/sessions/{id}/background/{work_id}/stop` and `POST …/mcp/{server} {action}`. The overview gains `background_work` (running only) and per-row `actions` (only those that fit the state).
  - Stop says "You stopped the … “title”." as a notice. The reply goes on.
  - **Claude Code (2.1.316, measured):**
    - `apply_flag_settings {effortLevel}` (null means default) is read back through `get_settings` `applied.effort`; anything else answers "next" (a restart);
    - the harness row says `effort_live`, and the chip stays open while a reply runs;
    - `stop_task` stops one task (`_stopping` keeps its notification from being said twice and from waking `_alone`);
    - `mcp_reconnect` and `mcp_toggle` are followed by `mcp_status` → `note_servers`. Turning a server off persists in the account's `.claude.json` for the folder, as `/mcp` does.
  - **Codex (0.157.0, measured):**
    - `thread/backgroundTerminals/list/terminate` (experimental) feed `CodexDriver.background_tasks` (`_terminal_count`), so the reaper and pool keep the session;
    - they are read after each turn and every `TERMINALS_POLL_S` while any run (no exit notification exists), and never written mid-turn (the newest request must stay the turn's);
    - `mcpServer/oauth/login` returns `authorizationUrl`, checked by `safe_sign_in_url` and opened by the page through `window.open` (the desktop's `openExternalIfSafe`);
    - `oauthLogin/completed` is said, for this thread only.
  - Pins: `tests/test_session_controls{,_routes}_v14410.py`, `dashboard/__tests__/session-controls-v1441-0.test.tsx`.

## Draft Handbook

**Background work and MCP servers in a coding session (Build › Code).** The Session tab lists what the coding tool still runs in the background: Claude Code's helpers and background commands, and Codex's background terminals. To stop one, open its ⋯ menu and choose Stop. Only that one stops; a reply that is running goes on, and the conversation says what you stopped. Each of the tool's own MCP servers has a ⋯ menu too, holding only what that tool can do:

- Claude Code: Reconnect, Turn off and Turn on. Like its own `/mcp`, it remembers "off" for this folder.
- Codex: Sign in… on a server that needs it. Codex opens its sign-in page in your browser; Iron Jarvis never signs in for you.

Changing Claude Code's effort now takes effect at once, even while it works.

## Review (independent, v1.441.0)

Re-measured on Claude Code 2.1.316 (scratch `CLAUDE_CONFIG_DIR` and `USERPROFILE`, the builder's stand-in Anthropic upstream, dummy key, no model call): a background `Bash` stopped with `stop_task`, then a second `stop_task` of the same id 50 ms later — both answered `{}`; `task_updated` (killed), `task_notification` (stopped) and `background_tasks_changed []` came BEFORE the first answer. An effort change (`apply_flag_settings high`) sent while a `can_use_tool` card was open was answered at once and `get_settings` said `high` — but the running reply's next request still carried `output_config.effort: "medium"`; only the user's NEXT message carried `"high"`. A backgrounded `Agent` (2.1.316 backgrounds it by default) carries `is_backgrounded: true` on `task_started`.

Defects found, each pinned red first (`tests/test_session_controls_review_v14410.py`, 9 pins; one dashboard case in `dashboard/__tests__/session-controls-v1441-0.test.tsx`):

1. **"Now works at high effort" was not true mid-reply.** The running reply keeps its effort (measured above). `ClaudeDriver.set_effort` still answers `now` (no restart is needed) but, while a reply runs (`turn_open` or `_alone`), says `EFFORT_NEXT_WORDS` ("… from your next message; the reply running now keeps its effort."). `xhigh` is said "extra" (`EFFORT_SAID`, the chip's own word).
2. **Iron Jarvis's own Codex terminal reads kept the session alive for ever.** Every answer to `thread/backgroundTerminals/list` passed through the service's `on_message`, which stamps `last_heard`, so the 30 s poll reset the v1.418.0 two-silent-hours rule (`BACKGROUND_SILENT_MAX_S`) on every read: a forgotten dev server held the process for ever. `Driver.quiet_frame(obj)` (base: False), `CodexDriver._quiet_ids` (the read's own request id, taken before any await), and `service.heard_from(driver, obj)` used by BOTH `on_message`s (the service's and the code host's adopt path).
3. **Two presses of Stop on one Claude Code task** sent two `stop_task`s and said "You stopped …" twice. A task in `_stopping` is refused (`TASK_ALREADY_STOPPING`) and left out of the list.
4. **Codex's stale-id refusal reached the screen raw** ("Codex did not stop it: invalid background terminal process id"): now `TERMINAL_ALREADY_ENDED`.
5. **The Stop route waited unbounded on a list read** (only for the row's kind; up to Codex's 60 s request timeout): now `LIST_TIMEOUT_S`, falling back to "task".
6. **The sign-in fallback link was always shown with words that read as if no page had opened** — `window.open(…, "noopener")` answers null by the HTML spec, and the desktop's window-open handler opens the page and answers null too, so the "blocked window" branch was the only branch. The link stays, worded "If no page opened: Open the sign-in page".
7. **Two of the builder's Codex claims had no pin** (a mutation of each stayed green): the terminal follow never reads while a turn is open, and the terminals end with the process (`on_exit`). Pinned.

Mutations: 8 of my fixes broken one at a time → each red; 5 of the builder's claims → 3 red, 2 survived (item 7) and are now red.

Checked and fine: both new routes are found by master's dead-code caller scan (`tests/test_wave6_dead_code_v14390.py` at `7abc8d31`, run against this branch; a made-up route reads uncalled); `safe_sign_in_url` and `openableSignIn` refuse `javascript:`, `file:`, `data:` and user:password addresses, the desktop opens a `window.open` through `openExternalIfSafe`, and the URL is never in a notice or the event log; Iron Jarvis writes nothing to `.claude.json` (only Claude Code's own `mcp_toggle` does); a stop is scoped to the session's own driver (Claude's task ids, Codex's `threadId`).

Still OPEN: an effort change sent while Claude Code is stuck can hold the PATCH up to 2 × 30 s before it falls back; the "remembers this for this folder" words are in a hover title (the notice after says it too); Codex terminal output still stamps `last_heard` (it is the tool speaking); `stop_background` and the sign-in are written mid-turn for Codex (a user press; `turn/started` names the turn, so a code-host restart in that window is safe).
