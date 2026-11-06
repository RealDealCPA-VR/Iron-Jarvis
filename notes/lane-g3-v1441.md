# Lane g3 (v1.441.0): Codex Plan, OpenCode's model list, agents, helpers and questions

## What changed, for the user

- **Codex has Plan in Build › Code.** It is Codex's own plan mode. Codex looks and plans, and changes no file: every file change it asks for is declined at once, and it asks before every command. Its plan is the reply. Under it waits the same plan card every tool uses: "Approve, then ask first" or Deny. Approve leaves Plan for Ask first, and Codex starts on the plan in the same reply, with its own words "Implement the plan." Deny keeps it in Plan.
- **Codex's Auto review stays off, and the mode menu says why**: a reviewer model of Codex's own would answer each approval instead of you, and Iron Jarvis cannot show you yet what it decided. **Auto edits stays off on Windows.** Its reason now also says that Codex's own Windows protection for commands is not set up on this PC, and that Iron Jarvis does not set it up.
- **OpenCode's model menu is filled before the first session**, from one quiet background read of `opencode models --verbose`. The read starts no session, makes no model call, and never fetches models.dev. Each model keeps its own effort levels.
- **The user's own OpenCode agents are modes.** Each primary agent that OpenCode lists appears in the mode menu by its name, before Full access, with its description. It runs as Ask first, except where the agent's own permission rules say otherwise. If a folder does not have that agent, the session says so and uses build.
- **OpenCode starts no helpers outside Full access.** Before, in Ask first or Auto edits, a helper whose step needed approval waited for ever, because its question never reached Iron Jarvis. Now `task` is refused, so the model does the work itself. A `task` rule of the user's own is kept.
- **OpenCode's question tool is never switched on**, because its question would never reach the screen.

## What was measured, and how

All OpenCode measurements: opencode 1.18.18 with a SCRATCH home (USERPROFILE, HOME and every XDG_* folder in the lane's scratch folder). The config named a stand-in OpenAI-compatible model server on 127.0.0.1 (`tests/fixtures/fake_openai_endpoint_v1387.py`) that scripted the model's tool calls. models.dev fetch, autoupdate, default plugins and LSP downloads were off. No vendor was reached. Scripts are in the scratch folder `g3/scripts/oc_probe.py`. The program's bundled ACP source was also read (`ACP.initialize`, the permission handler, the tool registry).

- `opencode models --verbose`: answers `<provider>/<model>` and then the model's JSON, with `variants` as the efforts. It took 1.7–2.2 s, exit 0. Recorded as `tests/fixtures/build_app_v14410/opencode_models_verbose.txt`.
- `configOptions.mode` lists build, plan and every non-hidden agent of the user's whose mode is primary or all. Subagents are never listed. `set_config_option mode=<agent>` works.
- An agent's own permission rules sit AFTER the config's rules and `OPENCODE_PERMISSION`. An agent with `"bash": "allow"` ran `echo hi` with no card under `{"*": "ask"}`. With `"bash": "deny"`, its bash tool was removed. Recorded as `oc_agent_docs.json`.
- **Helper hang.** With `task: allow` and `{"*": "ask"}`, a helper's `bash` sent nothing to the client for 40 s, and only `session/cancel` ended it ("Task cancelled"). The source shows why: `permission.handle` calls `session.tryGet(childSessionID)`, which is undefined for a helper's session, so the handler returns with no answer and no request. With `task: deny` (the fix), the model is not offered `task`, and the reply ended in 2.4 s.
- **Question tool.** It is not in an ACP session's tool list: the registry adds it only for `app`, `cli` and `desktop`, or with `OPENCODE_ENABLE_QUESTION_TOOL`. With that variable set and `elicitation` advertised, the question was never sent: no `elicitation/create` and no permission request. The ACP handler handles only `session.status`, `permission.asked` and message parts. The reply waited until Stop.

Codex measurements (codex 0.157.0):
- `generate-json-schema --experimental`. A scratch `CODEX_HOME` with no sign-in for: `collaborationMode/list` (the Plan and Default presets); `turn/start` with `collaborationMode {mode: plan, settings: {model}}`, which is accepted, while it is refused without `settings`; `approvalsReviewer: auto_review`, which is accepted; `windowsSandbox/readiness`, which answers `notConfigured`; and `thread/settings/updated`, which shows that collaborationMode is sticky across turns.
- **ONE tiny real model call** on the user's own ChatGPT login (plus plan). It used gpt-5.6-terra at effort low, on an EPHEMERAL thread in a scratch folder, so nothing was saved and nothing was changed (the file was unchanged afterwards). Plan mode streamed `item/plan/delta` ×27 and then `item/completed {type: plan, text}`, then `turn/completed`, with no agentMessage. Recorded and masked as `tests/fixtures/build_app_v14410/codex_plan.json`. The implement turn after the card was written by hand.
- The binary was read for Codex's own TUI flow ("Implement this plan?" → "Implement the plan.") and for the plan-mode instructions, which are instructions only and not a lock.

## Pins

- `tests/test_build_app_codex_plan_v14410.py` (12)
- `tests/test_build_app_opencode_agents_v14410.py` (19)
- `tests/test_build_app_opencode_models_v14410.py` (10)
- `dashboard/__tests__/build-app-plan-agents-v1441-0.test.tsx` (3), with its seam `dashboard/__tests__/fixtures/build-app-plan-agents-v1441-0.json` (`IJ_REGEN_BUILD_APP_V1441=1`)
- Moved on purpose:
  - `test_build_app_routes_v1409`: Codex now offers Plan, and Auto is the hidden mode.
  - `test_build_app_codex_posture_v1420`: the mode table, the Auto reason, and the Auto edits reason (now with the readiness fact).
  - `test_build_app_opencode_v1413` and `test_build_app_opencode_rules_v1420`: `task` is deny outside Full access.
- conftest: `_no_real_opencode_catalog_read` (the reader is off for the suite).

## Mutations (each break, its pins, then restore)

All 18 went red. M15 survived the first time: only the second of two guards had been removed. The stronger mutation, removing both guards, is red.

- M1: Plan never sends collaborationMode.
- M2: the thread's collab mode is not remembered.
- M3: a file change in Plan becomes a card.
- M4: no plan card before done.
- M5: Allow does not start the implement turn.
- M6: Stop is not known to the plan card.
- M7: the service never calls `answer_here`.
- M8: `task` is allowed again.
- M9: `OPENCODE_ENABLE_QUESTION_TOOL` reaches OpenCode.
- M10: another session's update is drawn.
- M11: no agent rows in the menu.
- M12: any agent name is accepted.
- M13: an unlisted agent is set anyway.
- M14: the reader may fetch models.dev.
- M15: the reader is not single-flight.
- M16: the background read never reaches the menu.
- M17: OpenCode's files are not watched.
- M18: an agent mode is not Ask first.

## OPEN

- Codex Plan is not kept across a code-host restart. A plan card that is waiting when the daemon restarts is lost, and the reply is closed as cut off.
- In Codex Plan, a command the user allows may still change files. Codex's own instructions say not to, but only file-change requests are blocked by Iron Jarvis.
- Codex Auto review: not offered. Drawing `item/autoApprovalReview/*` and `thread/approveGuardianDeniedAction` is the work left.
- Codex Auto edits after `windowsSandbox/setupStart`: not measured. The setup changes Windows and needs administrator rights.
- OpenCode helpers in Full access still work. A user's own `task` rule that allows helpers can still hang if a helper's step asks. That is the user's rule, and the rules note already says the user's rules apply.
- OpenCode agent modes are listed from what any OpenCode process last said (the union of folders). The menu can offer an agent that another folder does not have; the session then says so and uses build. Agent modes are not folder defaults (`folder_defaults` checks fixed modes).
- OpenCode's `question` tool and helper cards cannot be drawn until OpenCode sends them over ACP. A route through OpenCode's internal HTTP server (`/permission/:id/reply`, `/question/:id/reply`, its SSE events) could answer them. That was not built: it needs a known port and the server password, and it changes how OpenCode is started.
- Not driven in the packaged app or through a full scratch daemon with the real CLIs. The real opencode was driven directly over ACP, and the real Codex directly over app-server.

## Draft CLAUDE.md bullet

- **Codex has its own Plan in Build › Code; OpenCode's models, the user's own agents, no helpers that hang** (v1.441.0, lane g3).
  - CODEX PLAN (`hosted/codex.py`, measured on 0.157.0): `turn/start` sends `collaborationMode {mode: plan, settings: {model: self.model or the thread's model, reasoning_effort, developer_instructions: null}}` while in Plan. The thread keeps it, so leaving Plan sends `{mode: default}` once (`_collab`; resume reads the effective mode from `thread/resume`). A session that never planned sends no key, byte for byte.
  - Plan runs on Ask first's posture. Every file-change request is declined at once with a notice (`_decline_in_plan`, decided by `_turn_mode`), and commands still ask.
  - `item/plan/delta` and the `plan` item are the reply's text. At `turn/completed` with a plan, `_ask_plan` raises the shared plan card (tool `codex_plan`, `W.PLAN_CARD_TITLE`, `plan`, `then_mode: ask`, once-only, `native` None) before `done`.
  - The service calls `driver.answer_here` when `prepare_answer` returns None. Allow leaves Plan (`leaves_plan_to`; the service saves the mode) and starts `IMPLEMENT_TEXT` in the same reply. Deny ends the reply with `CODEX_PLAN_KEPT`. Stop (`stopping()`) ends it as interrupted.
  - Auto review (`approvalsReviewer`) and Auto edits are hidden, with measured reasons.
  - OPENCODE MODELS (`hosted/opencode_catalog.py`): `opencode models --verbose` through `core/procs.run`, on a background thread, single-flight, TTL 600 s, a watch on OpenCode's global config, `auth.json` and its models cache, retried 300 s after a failure. `OPENCODE_DISABLE_MODELS_FETCH=1` is set for that process only. `OCC.AUTO_PROBE` is off in conftest. `service._models(OPENCODE)` merges the read with a process's own list (`OCC.merged`).
  - AGENTS: `agent:<name>` modes come from `configOptions.mode` (`O.LAST_AGENTS`, per folder, kept in `hosted/opencode_agents.json`). They run as Ask first plus that agent: the agent's own rules sit after ours (measured). They are listed before Full access and accepted only when listed (`check_mode`, case kept). An agent the folder lacks gives a notice and build.
  - HELPERS: OpenCode's ACP side never sends a helper session's permission question (`session.tryGet`). So `permission_env` sets `task: deny` outside Full access, unless the user has a `task` rule. Updates naming another `sessionId` are not drawn.
  - QUESTION: never advertised to OpenCode. `OPENCODE_ENABLE_QUESTION_TOOL` is dropped from its env (measured: the question is never sent over ACP).
  - Pins: `tests/test_build_app_{codex_plan,opencode_agents,opencode_models}_v14410.py`, `dashboard/__tests__/build-app-plan-agents-v1441-0.test.tsx`.

## Draft Handbook

**Codex: Plan.** Choose Plan in the mode menu. Codex looks around and writes a plan, and changes no file while it plans. It still asks before it runs a command. When the plan is ready, the card under it asks you: "Approve, then ask first" starts the work at once in Ask first, and Deny keeps Codex planning. Tell it what to change, and it plans again. Codex's Auto review is not offered yet.

**OpenCode: your own agents and models.** OpenCode's model menu shows OpenCode's own model list, even before your first OpenCode session. Your own OpenCode agents (primary agents in your OpenCode config) appear in the mode menu by their names. They ask first, except where the agent's own permission rules say otherwise. Outside Full access, OpenCode does not start helper agents, because a helper's questions cannot reach Iron Jarvis and it would wait for ever.

## Review (independent reviewer)

Pins: `tests/test_build_app_codex_plan_review_v14410.py` (each red on the lane's first cut).

### Defects fixed

1. **A plan card answered after a restart hung the reply for ever** (Codex). The driver
   matched the waiting card by OBJECT (`self._held is ask`), and an Ask has no JSON form,
   so the code host's kept driver state came back with no card. `hosted/adopt` re-opens
   the card as a NEW Ask, so Allow/Deny returned at once doing nothing, `turn_open`
   stayed True and the reply never ended. Stop sent `turn/interrupt` for a turn Codex had
   already finished and waited the 60 s request timeout. Now the card is kept by its id
   (a string, kept in the state). Pins: the three `..._after_a_restart_...` tests
   (proved red by round-tripping `state()`/`restore()` and `adopt._ask_state/_ask_from`).
   The notes' OPEN line ("the reply is closed as cut off") was wrong for the code host;
   corrected below.
2. **Entering Plan dropped the user's own reasoning effort for the rest of the
   conversation** (Codex). RE-MEASURED (codex 0.157.0, scratch `CODEX_HOME` whose
   config.toml says `model_reasoning_effort = "high"`, no sign-in, no model call):
   `thread/start` answers `reasoningEffort: "high"`; a Plan `turn/start` with
   `reasoning_effort: null` made `thread/settings/updated` say `effort: null`, and
   leaving Plan with null kept it null. The collaboration mode now carries
   `self.effort or` the thread's own `reasoningEffort` (read from `thread/start` /
   `thread/resume`). Pins: `test_plan_keeps_the_threads_own_effort...`,
   `test_a_chosen_effort_still_wins...`; the builder's integration pin moved on
   purpose (`reasoning_effort: "medium"`, the recorded thread's own).
3. **Codex Plan's menu line said more than is held** ("changes no file"): only Codex's
   file-change requests are refused; a command the user allows can write. Reworded
   ("…and a command you allow can still change files"); pinned in the builder's file.
4. **"It starts no helpers here" did not say why.** `opencode.NO_HELPERS` now ends every
   mode line that refuses `task` (and the agent line) with the reason in plain words.
5. **An agent mode could not be kept as a folder's default**: the start box offers the
   save press for any mode, and `folder_defaults.clean_tool` answered "That mode is not
   one Iron Jarvis knows". It now keeps `agent:<name>` for OpenCode only (`_opencode_agent`);
   the composer still uses a kept mode only while the tool lists it.
6. Two doc comments orphaned in `hosted/words.py` (inserted between a comment and its
   declaration) were put back above their own constants.

Also pinned (was unpinned): a user's own `task` rule — exact, by wildcard, or per
subagent — is never overridden in any mode incl. an agent mode.

Also pinned: with the reader ON and OpenCode not installed, listing the tools
starts no read and still answers (the suite turns the reader off, which hid it).

### Mutations (reviewer; each break, pins, restore from bytes in a finally)

All red: R1 card matched by object again; R2 Stop ignores the kept card; R3/R4 the
thread's effort not carried / not read; R5/R6 an agent folder default refused / allowed
for any tool; R7 no reason for "no helpers"; R8 the deny overrides a user's own `task`
rule; R9 Plan's line overclaims again; the builder's M3 (file change in Plan a card),
M8 (`task` allowed), M9 (question switch reaches OpenCode), M14 (models.dev fetched);
B19 (the reader started with OpenCode missing) survived, then red after its pin.

### Re-measured

- The helper hang (opencode 1.18.18, scratch home, the builder's stand-in model server):
  with `task: allow` + `"*": "ask"` the helper's step sent nothing for 75 s, only
  `session/cancel` ended it; with the lane's `permission_env` (`task: deny`) the model
  was not offered `task` and the reply ended in 0.6 s. Refusing is right: the only way a
  helper's question could be answered is OpenCode's internal HTTP server.
- Codex effort under a collaboration mode (above).

### Still OPEN (reviewer)

- Codex Plan with no model known (an adopted v1.440.0 session with no model picked)
  sends no collaboration mode at all, so Codex does not plan; no notice says so.
- Shift+Tab (`nextMode`) steps into an agent mode; an agent whose own rules allow
  everything is then Full access in all but name, reached without two presses.
- The catalog reader's `opencode models` loads the plugins the user's config names, as
  any OpenCode run does (it may install one); the docstring's "no network of its own"
  is about models.dev only.
- An agent's own `permission` is not read for the session's rules notice; only the mode
  line says the agent's rules win.
