# Lane fix1 (v1.441.0): review findings A1-A6, P1-P5 (= D5), X3, X4

## What changed, for the user

- **A1. A note while Antigravity waits on a card or question.**
  - Before: the reply failed. Every later message failed too, and the card stayed listed.
  - Now the note is refused in plain words before anything is sent: "Antigravity is waiting for your answer on the card above — answer it first, then send your note."
  - The refusal is a plain 409 with no `not_working` code. The page puts the words back in the box and shows the line. A `not_working` code would make the page send the words as a new message.
  - Backstop: if Antigravity refuses a note's prompt anyway (a card arrived while the note was going out), the reply goes on under the prompt before it. The note goes back to the box when the reply ends, and a notice says so.
  - The same backstop covers the case where the earlier prompt had already answered: the turn ends on that answer, completed. It can no longer get stuck.
- **A2. Multi-select questions.** These are Antigravity's `ask_question` with `is_multi_select`.
  - The card now shows the choices as checkboxes, and the answer goes back as a LIST of choices.
  - Anything typed goes into `qN_other`.
  - The page's ", "-joined answer is matched back against the choice labels greedily, so a label that itself contains a comma still counts as one choice.
- **A3.** A question form with no question Iron Jarvis can show is still declined, but a notice now says so. Before, it was declined silently.
- **A4.** The sign-in error said "send your message again" twice. Now it says it once.
- **A5. The Iron Jarvis tools chip and Antigravity.**
  - The chip is hidden for Antigravity, because agy 1.5.2 does not report `mcpCapabilities`.
  - The session view carries `jarvis_tools: false`; the page decodes it as `jarvisToolsTaken: false`.
  - Ticking a box no longer restarts Antigravity, and no token is minted for it.
  - If an Antigravity handshake ever reports `mcpCapabilities.http`, the chip comes back. `jarvis_tools.note_takes` updates on every start.
- **A6.** An effort the model does not take is now also cleared from the session row and the chip, not only from the driver. The row is cleared only if it still holds that same effort.
- **P1. Picking a mode while Claude Code's plan card waits.**
  - The mode the user picks now stays after Allow, and Allow never puts the session back into Full access.
  - The mode the approved plan goes back to is read when the user answers (`ClaudeDriver.plan_exit_mode`), not when the card was raised.
  - Measured from claude 2.1.316's own bundle (`ExitPlanModeV2Tool`): Claude Code restores `prePlanMode` only while it is still in plan mode. So once the chip has moved, Claude Code already agrees with the chip.
- **P2. `/model` with Claude Code's own names.**
  - `/model opus`, `sonnet`, `haiku`, the `[1m]` forms and the picker's own `value` now resolve, through `claude_models.resolve_alias` (cached catalog only, never a start).
  - A name nothing resolves goes to Claude Code as typed, as in v1.438.0. It is no longer refused.
- **P3. The `/` command list, per folder.**
  - Claude Code's `/` list is now kept per folder (`LAST_COMMANDS["claude-code@<folder>"]`).
  - The bare key now holds only the rows Claude Code marks `builtin: true`. An older Claude Code that marks none keeps its whole list there.
  - The "needs its terminal" check runs only once Claude Code has said its own list for the session's folder. So a user's own `status`, `export` or similar command reaches Claude Code.
  - The `/` menu no longer shows another project's commands.
- **P4.** A rename made while the tool was stopped is now sent to the tool once, right after its next start.
- **P5 / D5.** A typed `/export` for Claude Code now names "Export as Markdown in the session's ⋯ menu" instead of pointing at the terminal.
- **X3.** The closed-window toast names Grok and Antigravity. Before, it said "Your coding tool".
- **X4.**
  - The from-terminal refusal (`W.NOT_A_CODING_TERMINAL`) now names all six tools.
  - The `build_folder_show` description and `_VIEW_REFUSAL` in settings/records_build.py now name Antigravity too.
  - For the integrator: `docs/HANDBOOK.md` line 3033 ("**Build › Code (Claude Code, Codex, Pi, OpenCode, Grok)**") and line 3550 ("your own Claude Code, Codex, Pi, OpenCode or Grok does the work") still leave out Antigravity. Line 3555 has the same list.

## Measured, and how

- **A2.** Measured on the real agy 1.5.2, with a scratch USERPROFILE and the stand-in model server (`scratchpad/agy/scripts/standin.py` + `drive2.py`).
  - Answer sent: `{action: "accept", content: {q1: ["1", "3"], q1_other: "Purple", q2: "2"}}` for `is_multi_select`.
  - The model received the function response "A1: Blue, Green, Purple\nA2: M".
  - Files: scratchpad/fix1/m1/run.json and req.log.
- **A1.** The refusal text agy gives a second prompt was measured by the reviewer.
- **P1.** Read from `C:\Users\VR\.local\bin\claude.exe` (2.1.316) with a grep. In `ExitPlanModeV2Tool`, `if(O.mode==="plan"&&…){ let i=O.prePlanMode??"default" … }`: the plan exit changes the mode only while it is in plan.
- **P3.** Measured on claude 2.1.316, with no model call:
  - Setup: `claude -p --input-format stream-json --output-format stream-json --verbose --strict-mcp-config` in a scratch folder holding `.claude/commands/projfoo.md`, with a scratch `CLAUDE_CONFIG_DIR` (signed out), sending only an `initialize` control request.
  - The 50 commands carry the keys name, description, argumentHint, builtin and aliases.
  - `projfoo` has no `builtin` key and its description ends "(project)". Every one of Claude Code's own rows, bundled skills included, has `builtin: true`.
- No model call was made anywhere, and nothing was signed in.

## Pins

All mutation-checked; results are in the Mutations section.

- `tests/test_build_app_agy_asks_v14410.py` (19 tests): A1 at driver level (card and question) and through the steer route on the recorded `a_cmd_allow`; both backstop cases; a refused message prompt still fails; A2 to A6.
- `tests/test_build_app_daemon_parity_v14410.py` (23 tests): P1 through the real service and fake Claude Code (pick Accept edits; start in Full access, then Plan, then pick Ask first, then Allow gives Ask first; no pick still leaves Plan); P2, P3, P4, P5, X3 (node), X4.
- `dashboard/__tests__/build-code-jarvis-tools-hidden-v1441-0.test.tsx` (3 tests): the chip is hidden only for an explicit `jarvis_tools: false`.

Moved on purpose:
- `test_build_app_parity_v1439`: `/status` with an empty list is now sent through; `/model sonnet-9` reaches Claude Code.
- `test_build_app_routes_v1409`: the from-terminal words.
- The golden fixture `build-app-events-v1439.json` was regenerated with `IJ_REGEN_BUILD_APP_AGY=1`. Only the sign-in title changed.

## Still OPEN

- A rename made while stopped is held in memory only. If Iron Jarvis restarts before the tool next starts, the name is not sent.
- After a restart, the bare Claude Code built-in list is empty until a Claude session opens. Until then, the start composer's `/` menu for Claude shows nothing. That is the same rule as before for an empty list.
- P1: the plan card's "Approve, then ask first" label stays on screen after the chip moves. Making Enter / the label follow `thenMode` is the dashboard lane's D3.
- A5: what an Antigravity process says about MCP is kept in memory only. Antigravity starts as "takes none", the measured 1.5.2 truth.

## Draft CLAUDE.md bullet

- **The review's Antigravity and parity findings: a note never wedges Antigravity, its multi-select questions work, the chip and the row tell the truth, a plan approval never overwrites the chip** (v1.441.0, lane fix1).
  - A1: `AntigravityDriver._asking` holds the native ids of open cards and questions. `steer` refuses with `W.ANTIGRAVITY_NOTE_WHILE_ASKING` (a plain 409, never `not_working`). A refused note prompt goes to `_note_refused`: the turn goes back under the prompt before it, or ends on the last superseded answer, and the note is handed back.
  - A2: `form_questions` reads `type: "array"` / `items.anyOf` as `multi`. `form_answer` sends a list of consts and matches the page's ", "-joined answer greedily.
  - A3: a form with no question is declined and said.
  - A5: `jarvis_tools.takes()` holds `{antigravity: False}` and is updated by each start's `mcpCapabilities.http`. The view carries `jarvis_tools: false`, the page hides the chip, and there is no restart and no token.
  - A6: `on_effort_dropped` calls `store.clear_effort_if`.
  - P1: `ClaudeDriver.plan_exit_mode` reads the way back AT the answer, and only while the driver is still in plan (claude 2.1.316's ExitPlanModeV2Tool does the same).
  - P2: `T.resolve_claude_model` falls back to `claude_models.resolve_alias`. A name nothing resolves is sent through.
  - P3: Claude Code is in `FOLDER_COMMANDS`, with the bare key holding the `builtin: true` rows (measured). `service._own_commands` is what the typed check reads, and nothing is refused before it has been read for the folder.
  - P4: `HostedService._renames` is told after the next `driver.open`.
  - Pins: `tests/test_build_app_{agy_asks,daemon_parity}_v14410.py`, `dashboard/__tests__/build-code-jarvis-tools-hidden-v1441-0.test.tsx`.

## Draft Handbook lines

- In Antigravity, a note typed while a card or a question waits is refused, and your words stay in the box. Answer the card first. A question that lets you pick several answers shows checkboxes.
- The Iron Jarvis tools chip is not shown for Antigravity, because it does not take them (agy 1.5.2).
- In Claude Code, `/model opus`, `/model sonnet` and `/model haiku` set the model chip. A name Iron Jarvis does not know goes to Claude Code as typed.
- If you pick a mode while a plan card waits, approving the plan keeps the mode you picked.
- Type `/export` to be pointed at Export as Markdown in the session's ⋯ menu.

## Mutations

All were run with `scratchpad/fix1/mutate.py`, which breaks one fix, runs its pins, and restores the file in a `finally`.

All of these went red:
- A1: both steer checks removed; backstop removed; `answered` keeps the ask.
- A2: multi read as single; greedy label matching replaced by a plain split.
- A3: the notice removed.
- A4: the old words.
- A5: the store key removed; the restart kept; the `takes` default flipped to true.
- A6: the effort kept; the row cleared regardless of what it holds.
- P1: the card's stale `leaves_plan_to` used; the driver ignoring the chip.
- P2: no alias (the service pin hits its 15 s bound and fails); unresolved refused.
- P3: an empty list refuses; one global key; the bare list holds everything.
- P4: the pending rename dropped.
- P5: the terminal words.
- X3: the old map.
- X4: the old words.
- Dashboard: the hide condition removed; the decode removed.

Not red, by design:
- Removing ONE of the two steer checks: the other catches it (the second one guards the await on `_prompt_blocks`).

Real agy 1.5.2 through the real service (scratch home, stand-in model; the review's probes, run once and not committed):
- A note while a command card is open: 409 with the words; Allow, then completed, with no error.
- Multi-select: the card showed three choices with `multi: true`; the model saw "A1: Blue, Red".
