# Lane g4 (v1.441.0): Build › Code gaps from the parity table's "Left for later"

## What changed, for the user

1. **Grok's own conversation shows in the Code view.** A Grok session opened
   from Grok's saved sessions (or brought back from a terminal) now shows what
   was said before: the user's messages, Grok's answers, each step it ran and
   its result. Read from Grok's own `chat_history.jsonl`, never written.
2. **Grok's MCP servers in the Session tab.** The Session tab lists Grok's own
   MCP servers by name with their state (Connected, Starting…, Did not start
   and why, Needs you to sign in — with how: Open in terminal, `/mcps`, press
   i). No address, key, command or Grok's error detail is ever kept or shown.
3. **Share my profile with Grok.** A fifth "Share with Build" switch (off by
   default) writes the profile block into Grok's own `AGENTS.md`
   (`<GROK_HOME or ~/.grok>/AGENTS.md`, and each Iron-Proxy xAI account's
   home). The offer appears in a new Grok session like the others.
4. **Pi `!command` and `!!command`.** In a Pi session, a message that starts
   with `!` runs that command through Pi's own RPC `bash`, shown as one command
   step with its output as it comes and its exit. `!!` runs it the same way but
   keeps it out of the conversation (Pi's model never sees it; one quiet line
   says so). Stop asks Pi to stop the command. A bare `!` is refused in words.
   A typed `!` command used to reach the MODEL as plain text.
5. **Antigravity's own conversation shows in the Code view** (from its
   `brain/<id>/.system_generated/logs/transcript.jsonl`), like the others.
6. Pi's imported transcript now also shows the commands typed with `!`/`!!`.
7. Pi's Session tab line now says how to see Pi's MCP servers: "Type /mcp in
   the box and Pi says each one's state."

## What was measured, and how

- **Grok `chat_history.jsonl`** (grok 1.0.70): read the shape (keys and types
  only) of the sessions Grok saved for Iron Jarvis's own earlier scratch
  recordings under `~/.grok/sessions` (READ only, never written). Records:
  `system` (Grok's prompt), `user` (`prompt_index` = the user's words in
  `<user_query>`; `synthetic_reason` `system_reminder` = Grok's own, an
  `interjection` = a note sent while working; the first plain user record is
  Grok's workspace note), `reasoning` (encrypted), `assistant` {content,
  tool_calls [{id, name, arguments as JSON text}]}, `tool_result`
  {tool_call_id, content}. NO times in the file: they come from the same
  folder's `events.jsonl` (`turn_started` turn_number == prompt_index, ts;
  `tool_completed` tool_call_id, ts), else `rewind_points.jsonl`. NOTE:
  `events.jsonl` also holds `mcp_server_starting` rows whose `target` is the
  MCP URL with its key — only the turn and tool time fields are read.
- **Grok MCP notifications**: `grok agent --no-leader stdio` with a scratch
  `GROK_HOME` + `USERPROFILE` whose `config.toml` had a working stdio server
  (a tiny Python MCP), an HTTP server on a closed port with a made-up key in
  its URL and header, a turned-off server, and a `[model.localstub]` entry
  (`base_url` a closed local port, made-up key) so `session/new` opened with
  NO xAI sign-in and NO model call. Recorded: `_x.ai/mcp/servers_updated
  {mcpServers: [{name, source, type, url | command, args}]}` (config only, URL
  with key), `_x.ai/mcp/init_progress`, `_x.ai/mcp/server_status {sessionId,
  name, source, status: ready | unavailable, reason: initialized |
  handshake_failed, detail (repeats the URL), tools}`, `_x.ai/mcp_initialized`.
  The `_x.ai/mcp/list {sessionId}` request answers every server with
  `session {enabled, status, tools}` (incl. the turned-off one); not used. The
  binary's other reason words (`spawn_failed`, `timeout`, `auth_required`,
  `client_error`) come from `strings` of grok.exe. Without a stand-in model,
  `session/new` answers "Authentication required" AFTER servers_updated.
  Fixture: `tests/fixtures/build_app_v14410/grok_mcp_notifications.json`.
- **Grok rules file**: `grok inspect --json` on a scratch GROK_HOME and
  USERPROFILE (no sign-in, no model call). With `AGENTS.md`, `CLAUDE.md`,
  `AGENT.md` and `rules/iron-jarvis.md` in the Grok home, all four were listed
  as `scope: global` (Grok loads every one, it does not pick the first).
  `~/.claude/CLAUDE.md` is ALSO listed (Claude compatibility, on by default).
- **Pi `bash`** (pi 1.3.0): read `dist/modes/rpc/rpc-mode.js` (`case "bash"`,
  `abort_bash`) and `dist/core/agent-session.js` (`executeBash`,
  `recordBashResult`, `bash_execution_update {id, delta}`), then ran
  `pi --mode rpc --no-session --offline` with a scratch PI_CODING_AGENT_DIR
  and sent `bash` commands (no model call): updates stream per chunk with the
  request's id; answer `{output, exitCode, cancelled, truncated}`; `get_messages`
  shows `bashExecution` records, `excludeFromContext: true` for `!!`.
  Fixture: `tests/fixtures/build_app_v14410/pi_user_bash.json`.
- **Pi MCP list**: rpc-mode.js has no MCP command; `dist/extensions/mcp/
  index.js` reports only through `ctx.ui.notify`. Measured with a scratch
  `mcp.json` naming a missing program: Pi pushed "MCP servers need attention:
  deadone: failed: MCP connection closed …" at start, and a typed `/mcp`
  (listed by `get_commands` as an extension command) answered a status notice.
  FINDING: no structured list; the Session tab keeps "does not report" and now
  says to type /mcp.
- **Antigravity transcript** (agy 1.5.2): read the scratch home the earlier
  stand-in runs left (`scratchpad/agy/home/.gemini/antigravity-cli/brain/<id>/
  .system_generated/logs/transcript.jsonl` and `transcript_full.jsonl`; keys,
  types, sources and statuses tabulated). Steps: USER_INPUT (USER_EXPLICIT,
  `<USER_REQUEST>`), PLANNER_RESPONSE (content, thinking, tool_calls with args
  as JSON text), GENERIC (one per call, "Created At/Completed At" header, ERROR
  status), CHECKPOINT, SYSTEM_MESSAGE, ERROR_MESSAGE. `transcript.jsonl` is
  used (the compact one; `truncated_fields` marks a cut field).
- **Antigravity usage windows**: `agy --help` (no usage/quota flag or
  subcommand), every recorded ACP run (`scratchpad/agy/*.json`: no quota, no
  `usage_update`, only the prompt answer's per-call token `usage`), the ACP
  logs (only `quotaProject=` in the auth line). `usage_update` appears 3 times
  in the binary's strings but was never sent in any recorded run. FINDING: agy
  reports no usage window over ACP; `/usage` (`/quota`) is its terminal's. The
  Usage tab already says "Antigravity does not report its account's limits
  here, so they are not known." (`dashboard/lib/codePanes.ts` NO_WINDOWS_TOOLS).

## The pins

- `tests/test_build_app_tool_transcripts_v14410.py` (8): Grok and Antigravity
  readers, times, the MCP key in Grok's events file never imported, cached and
  read-only, a session found by folder name only, Pi `bashExecution`, and both
  tools through the real service (a conversation opened from disk).
- `tests/test_build_app_grok_mcp_v14410.py` (5): the recorded notifications
  fold to names and states only, sign-in/gone servers, the Session tab words,
  the driver keeps its reported list apart from the servers Iron Jarvis hands
  Grok, and through a running fake Grok nothing secret is kept.
- `tests/test_build_app_pi_user_bash_v14410.py` (5): the typed reading, `!`
  through Pi's bash as one step, `!!` kept out + a failing exit, a bare `!`
  refused, Stop sends `abort_bash`.
- `tests/test_profile_share_grok_v14410.py` (6): the spec, schema row and
  writer keys; the view; byte-identical outside; GROK_HOME; a hand edit kept; a
  hard-linked file left alone; the real `~/.grok/AGENTS.md` untouched.
- `dashboard/__tests__/grok-share-offer-v1441-0.test.tsx` (2).
- Moved on purpose: `test_build_app_grok_v1416::test_groks_own_notifications_
  are_never_drawn` (the MCP list is read for names only now) and
  `test_build_app_tool_setup_v14260::test_a_session_whose_tool_never_said_says_
  not_yet` (Pi's note gained the /mcp sentence).
- Shared fixture: `tests/fixtures/fake_harness_v1409.py` gained
  `id_of_command` (a frame naming a Pi command's id without answering it).

## OPEN

- Grok servers turned off in its config are not listed (only `_x.ai/mcp/list`
  names them; not asked — one more request per start).
- Grok's MCP `status` words beyond ready/unavailable were not seen live; an
  unknown word reads "Did not start". Sign-in state is read from
  `reason: auth_required` (from the binary's strings, not seen live).
- Grok also reads `~/.claude/CLAUDE.md` (Claude compatibility, on by default),
  so with both Claude Code's and Grok's share on, Grok sees the profile twice.
  Not guarded; the row does not say it.
- PRE-EXISTING NAME CLASH: on an ACP driver `mcp_servers` is the list Iron
  Jarvis HANDS the tool; on the base driver it is the list the tool REPORTED.
  Grok now keeps its reported list as `reported_mcp`; the clash itself is left.
- Pi `!` runs under Pi's own rules (Pi asks nothing; an extension's `user_bash`
  hook may take it) — Iron Jarvis shows no card, as Pi's terminal shows none.
  A `!` typed while a reply runs waits in the queue like any message.
- Grok and Antigravity pictures in a past message show as "(a picture)"/nothing.
- Antigravity: no usage windows (see above); its MCP connection is not offered
  (agy reports no `mcpCapabilities`, unchanged).

## Draft CLAUDE.md bullet

- **Grok's and Antigravity's own conversations are imported; Grok's MCP servers
  are NAMES and states only; Grok gets a profile share; Pi runs `!`/`!!`
  through its own bash** (v1.441.0, lane g4). `hosted/history.transcript_source`
  finds Grok's `<home>/sessions/<folder>/<id>/chat_history.jsonl` and agy's
  `brain/<id>/.system_generated/logs/transcript.jsonl` by folder NAME;
  `_grok_events` takes times from the same folder's `events.jsonl`
  (`turn_started`/`tool_completed` only — that file also names every MCP URL
  with its key), `_antigravity_events` pairs each GENERIC step with the oldest
  open call; Pi's `bashExecution` records become command steps (`kept_out` for
  `!!`). `tool_setup.grok_servers`/`grok_status` read ONLY `name`, `status`,
  `reason` from `_x.ai/mcp/servers_updated`/`server_status` (never url,
  command, args, headers or `detail`); GrokDriver keeps them as
  `reported_mcp` because an ACP driver's `mcp_servers` is what Iron Jarvis
  hands the tool (`note_servers(attr=)`); REPORTS_MCP gains grok, sign-in words
  point at `/mcps`. `profile/share.CLIS["grok"]` = `<GROK_HOME or ~/.grok>/
  AGENTS.md`, proxy `xai`, setting `profile_share_grok` (ask, put=False,
  `_SHARE_KEYS`/`_HOOK_KEYS`), `shareCliOf("grok")`. Pi: `typed_builtin` reads
  `!`/`!!` (`SHELL_BUILTINS`), `PiDriver._run_shell` sends RPC `bash
  {command, excludeFromContext}`, draws one step from `bash_execution_update`
  (≤ 1 update/s) and the answer, Stop sends `abort_bash`; a bare `!` is a 400
  (`SHELL_NEEDS_COMMAND`). Measured: grok 1.0.70 (`grok inspect --json`, a
  scratch GROK_HOME with a stand-in model entry), pi 1.3.0 (`--no-session
  --offline`), agy 1.5.2 (stand-in runs). Pins: `tests/test_build_app_{tool_
  transcripts,grok_mcp,pi_user_bash}_v14410.py`, `tests/test_profile_share_
  grok_v14410.py`, `dashboard/__tests__/grok-share-offer-v1441-0.test.tsx`.
  Findings: Pi has no MCP list over RPC (only notices; `/mcp` typed answers
  one); agy reports no usage window over ACP.

## Draft Handbook text

**Coding sessions: what each tool brings along.** When you open a Grok or
Antigravity conversation you started in a terminal, the Code view now shows
what was said before, read from the tool's own saved files (they are never
changed). Grok's own MCP servers appear in the Session tab by name with their
state; if one needs a sign-in, press Open in terminal, type `/mcps`, choose the
server and press i. Iron Jarvis never shows or keeps a server's address or key.
In a Pi session you can run a command yourself: start the message with `!`
(Pi's model sees the command and its output) or `!!` (it doesn't). Settings ›
Memory & you › Share with Build now has a switch for Grok too.
