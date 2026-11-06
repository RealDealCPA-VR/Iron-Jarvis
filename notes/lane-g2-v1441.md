# Lane g2 (v1.441.0): Branch from here and Rewind to here in Build › Code

## What changed, for the user

- Each of your own messages in a Build › Code session has a quiet ⋯. It is shown on hover and keyboard focus, and always on touch. It lists only what the session's tool can do:
  - **Branch from here** makes a NEW session in the same folder, on the same tool, model, mode and account. The new session holds the conversation BEFORE that message, and opens with the message in its box, ready to edit and send. The original session stays exactly as it was.
  - **Rewind to here** drops that message and everything after it from the conversation. It takes two presses, and the message comes back to the box. Your files are NOT changed: the Changes pane's per-file Revert stays the way to put a file back. The quiet line under the box says what was done.
- The session row's ⋯ menu gains **Branch**, which branches the whole conversation.
- Neither action runs while a reply runs, while the tool works in the background, while the session is archived, or while the conversation is open in a terminal (only one place writes to a conversation).
- What each tool offers:

  | Tool | Branch (whole) | Branch from a message | Rewind |
  |---|---|---|---|
  | Claude Code | yes | yes | yes |
  | Codex | yes | yes | yes |
  | Pi | yes | yes | yes |
  | OpenCode | yes | no; a request names the message, the whole conversation is branched, and the line says so | no |
  | Grok, Antigravity | no | no | no |

- An older Iron Jarvis (no `branch` in the view, no `can_branch` on rows) shows none of this.

## What was measured, and how (no model call anywhere)

### Claude Code 2.1.316

Measured against a stand-in Anthropic server (`scratchpad/g2/anth_standin.py`) with a scratch `CLAUDE_CONFIG_DIR` and a fake API key:

- The `uuid` a stream-json user message is sent with IS its transcript record's `uuid`.
- `--resume H --fork-session --session-id N --resume-session-at <entry>`:
  - makes conversation N, holding H's messages up to that chain entry (the stand-in received exactly those);
  - leaves H untouched;
  - writes `N.jsonl` only with N's first message (an `initialize` alone writes nothing), keeping the copied uuids.
- `--resume H --resume-session-at <entry>` (no fork) answers from the cut history.
- A later plain `--resume H` follows that new branch, but only once a message was sent. So a rewind is kept in `branch.json` until the next message lands in the file.
- `rewind_files` (dry run) answers `canRewind:false`, "File rewinding is not enabled.", so files are never restored.

### Codex 0.157.0

Measured with `codex app-server` against a stand-in Responses API (`scratchpad/g2/resp_standin.py`) in a scratch `CODEX_HOME`. The schema comes from `generate-json-schema --experimental`.

- `thread/fork` (`beforeTurnId`, `excludeTurns`) and `thread/revert` (`beforeTurnId`) both survive a restart, and the next turn's model input held only the kept turns.
- Turn ids are kept in a fork.
- **Found end to end:** after a fork, the app-server that made it stays the fork's WRITER even after `thread/unsubscribe`. The branch's own start was refused: "thread … already has an active writer". So that process is stopped after a fork.

### Pi 1.3.0

Measured with `pi --mode rpc --offline --no-mcp` on a hand-made session file in a scratch `PI_CODING_AGENT_DIR`:

- `get_fork_messages` answers a list of `{entryId, text}`.
- `fork {entryId}` writes a new file holding the messages BEFORE that entry, and switches the process to it.
- `clone` copies the whole conversation into a new file and switches too.
- The original file is untouched.
- `switch_session` works, but it is not used: the process is stopped instead, and the original reopens with `--session-id`.

### OpenCode 1.18.18

Measured with `opencode acp` in scratch XDG folders:

- `sessionCapabilities.fork` is offered.
- `session/fork {sessionId, cwd, mcpServers}` answers a new `sessionId`.
- The ACP code forks the WHOLE session (read from the binary), and there is no rewind over ACP.

### Grok and Antigravity

Their ACP `sessionCapabilities` include no fork (`grok.py`, `antigravity.py`).

### End to end, in process

The real routes and service were driven against the REAL Claude Code and the REAL Codex, each against its stand-in (`scratchpad/g2/e2e_claude.py`, `e2e_codex.py`). For both:

- The branch's model input held the history up to the chosen message, then the new message.
- After a rewind, the next turn's input dropped the rewound turns.
- The turn after that continued the new branch.
- A pending Claude branch was settled by its first message.

## How a message is named

- By its `user` event's `at`. No new event key, so the goldens are unchanged.
- The tool's own reference is written beside the log in `<home>/hosted/<sid>/refs.json` right after the send:
  - Claude Code: its message uuid;
  - Codex: its new turn id, only if the turn id changed;
  - Pi: none. Matched by text against `get_fork_messages`, newest last, and refused in words when the lists differ.
- A reply's end waits for that write (`LiveSession.ref_save`). Under load the reply could end first, and a rewind right after was refused "did not record". Pinned with a slow `note_sent`.
- A message sent before this version, a message from the tool's own saved history, or a carried-on message offers no per-message action.

## The log and the view agree

- A rewind first copies the whole old log, `meta.json` and `refs.json` to `<home>/trash/<stamp>/hosted-rewind/<sid>/`.
- It then writes the history as the view showed it before the message: the log, plus the tool's transcript when it was merged in.
- It removes `outside.json`, so nothing rewound away comes back from the tool's file. Claude Code keeps the old branch until the next message.
- A fresh LiveSession then reads the cut log.

## Pins

- `tests/test_build_app_branch_v14410.py` (20 tests): the real routes, service and store, with fake Claude/Codex, and stub Pi/OpenCode drivers built from the measured answers.
- `dashboard/__tests__/build-code-branch-v1441-0.test.tsx` (9 tests).

Mutations: 26, all red.

| | Mutation |
|---|---|
| M1 | No `--fork-session` |
| M2 | No `--resume-session-at` |
| M3 | Parent read as the message itself |
| M4 | A rewind keeps the message |
| M5 | No "a reply runs" check |
| M6 | No terminal check |
| M7 | The Codex ref is not checked as new |
| M8 | Pi's text match is not checked |
| M9 | A Pi fork keeps its process |
| M10 | The transcript is merged again after a rewind |
| M11 | `settle` always settles |
| M12 | A terminal opens a pending branch |
| M13 | OpenCode's fork updates are drawn |
| M14 | No reference noted |
| M15 | A tool with no fork offers one |
| M16 | A branch of a rewound Claude session forks the old leaf |
| M17 | A Codex fork keeps the writer |
| M18 | A reply's end does not wait for the reference |
| D1 | A menu for a tool with neither |
| D2 | Rewind takes one press |
| D3 | `busy` is ignored |
| D4 | The row's Branch shows always |
| D5 | The rewound text is not put in the box |
| D6 | A menu on every message |
| D7 | The branch's box is empty |

Also run green:
- Python: the v1409 goldens, routes and review files; pi, opencode, grok, antigravity, history, queue, steer, carry-on, handover and code host; the three vocabulary files.
- Dashboard: build-app-view, history-keys, sessions, stream-rows, parity, answers, pictures-steer, opencode, pi, vocabulary.
- `npx tsc --noEmit`.

## OPEN

- A Claude Code branch exists only once its first message is sent. Until then, Open in terminal and a second branch are refused in words.
- Per-message actions exist only for messages sent from this version on. "Branch" from the end works for any conversation.
- Pi: a message an extension handled (for example `/plan`), or messages typed in Pi's own terminal, make the lists differ, and the action is refused in words.
- Rewind puts back the message's words, not its attached files.
- Two user messages within one 15.6 ms clock tick would share an `at` (practically never: one message per reply).
- Codex: a branch and a rewind each restart its app-server (the writer lock).
- Not driven in the packaged app. Pi and OpenCode were not driven end to end through the service: their RPC was measured offline, the flow was pinned with stubs.
- `tests/fixtures/fake_harness_v1409.py`: with `--fork-session` it answers under `--session-id`, as the real Claude Code does.

## Draft CLAUDE.md bullet

- **Build › Code's Branch from here / Rewind to here go through each tool's OWN way, and a tool with none offers none; files are never touched** (v1.441.0, `hosted/branching.py`, routes `routes/build_app_branch.py` `POST /build/app/sessions/{id}/fork {at?}` and `…/rewind {to}`).
  - A message is named by its `user` event's `at`. The tool's reference is written beside the log (`refs.json`): Claude Code's message uuid, Codex's turn id (only when the turn id changed), none for Pi (matched by text against `get_fork_messages`, newest last; refused when the lists differ). A reply's end waits for that write (`LiveSession.ref_save`).
  - CLAUDE CODE: no process at fork time. The new row gets a new uuid plus `branch.json` `{from, at, fork}`, and `ClaudeDriver.argv` (`fork_from`/`resume_at`, set by `prepare_driver`) starts `--resume H --fork-session --session-id N --resume-session-at <parentUuid of the message>`.
  - A Claude rewind writes `{at}` the same way. `_settle_branch` clears it once the turn's own uuid is in the conversation's file. Until then Open in terminal is refused and Claude Code still follows the old leaf: a branch from the end in between uses the rewind's entry.
  - CODEX: `thread/fork {beforeTurnId, excludeTurns}`, then `thread/unsubscribe` AND the process is stopped (measured: it stays the fork's writer, and the branch's start is refused "already has an active writer"). `thread/revert {beforeTurnId}`, then stop.
  - PI: `fork {entryId}` / `clone` SWITCH the process, so it is stopped (the original reopens with `--session-id`). A rewind is a fork the row moves onto.
  - OPENCODE: `session/fork` whole only (`_loading` hides its updates), then `session/close`. No rewind.
  - Grok and Antigravity: neither.
  - Refused: while a reply runs, while background work runs, while archived, while open in a terminal (one writer), and while a Claude branch has not had its first message.
  - A rewind copies the old log to `trash/<stamp>/hosted-rewind/<sid>/`, writes the history as the view showed it (merged transcript included) and removes `outside.json`, so the view and the log agree and nothing rewound comes back.
  - The view carries `branch {fork, fork_at, rewind, at, note?}`, and rows carry `can_branch`. CodeConversation's `MessageMenu` (`messageActions`) and the row's `code-row-branch-<id>` read them; absent = nothing shown.
  - Pins: `tests/test_build_app_branch_v14410.py`, `dashboard/__tests__/build-code-branch-v1441-0.test.tsx` (26 mutations red).

## Draft Handbook text

**Branch from here and Rewind to here (Build › Code).** Point at one of your messages and press ⋯.

- **Branch from here** opens a new session with the conversation before that message, on the same tool, model, mode and account. The message waits in its box for you to change and send. The session you branched from is left as it was.
- **Rewind to here** (press it twice) takes that message and everything after it out of the conversation, and puts the message back in the box.

Neither changes your files: to put a file back, use Revert in the Changes pane. "Branch" in a session's ⋯ menu branches the whole conversation.

What each tool supports:

| Tool | What it offers |
|---|---|
| Claude Code, Codex, Pi | All three. With Claude Code the branch is made with your first message in it. |
| OpenCode | Branch, of the whole conversation only. |
| Grok, Antigravity | Neither. |

Not while a reply runs, and not while the conversation is open in a terminal.

## Review (independent reviewer)

Each defect below is pinned in `tests/test_build_app_branch_review_v14410.py` (14 tests). Every pin was red on f7f9a7bf first, then green.

1. **A Claude Code rewind not yet sent could be opened in a terminal.**
   - What went wrong: a plain `claude --resume H` there opens the conversation as it was BEFORE the rewind. The draft bullet says "Until then Open in terminal is refused", but that held only for a branch: `pending_fork` reads `from`, and a rewind has none.
   - Proof, re-measured on real claude 2.1.316 with the builder's stand-in and a scratch home: after two messages, `-p --resume H` plus a new message sent the model `alpha, reply 1, beta, reply 2, delta`, so the rewound-away message came back.
   - Fix: `branching.pending_rewind` and `PENDING_REWIND_TERMINAL`, refused in `open_terminal`.
   - Pin: `test_a_claude_rewind_not_yet_sent_is_not_opened_in_a_terminal`.
2. **What a rewind dropped came back whenever the transcript was merged again.**
   - When: the conversation lives outside the view again (`mark_outside` on Open in terminal), or the log is empty after a rewind to the first message.
   - Why it happened:
     - Claude Code keeps the old branch in its file, and `history._claude_events` reads that file linearly.
     - Iron Jarvis's Codex reader has no revert handling.
     - Removing `outside.json` only held until the next `mark_outside`.
   - The first pin passed vacuously: two turns a few milliseconds apart sit inside the merge's 2 s `EDGE_S` of the kept turn. It now waits past that edge.
   - Fix: `rewound.json` (`note_rewound`), written by `_cut_log` and copied into a branch of a rewound session. `_with_transcript` drops the transcript entries dated at or before it (`without_rewound`).
   - Pins: `test_what_a_rewind_dropped_never_comes_back_when_the_conversation_goes_outside`, `test_a_codex_rewind_to_the_first_message_shows_an_empty_conversation`, `test_a_branch_of_a_rewound_session_never_merges_what_the_rewind_dropped`.
3. **A message whose send raised was recorded as sent** (`_note_sent` ran in a `finally`).
   - Effect: it was offered for a branch. For Pi it also broke the text match of every later branch or rewind: a refused `PiStillWorking` send stays in Pi's list forever.
   - Fix: the reference is noted only after `send_user` returns. On an exception, the reply-end future is still set.
   - Pin: `test_a_message_the_tool_never_received_is_not_recorded_as_sent`.
4. **A Pi fork or clone could leave the process switched to the fork.**
   - When: the fork went through but its new id could not be read (`get_state` refused).
   - Why: the stop came after that read. The next message then went into the wrong conversation.
   - Fix: fork and rewind stop the process in a `finally` once a request that changes the conversation went out (`asked`). A refusal before that point (the lists differ) still stops nothing.
   - Pin: `test_a_pi_fork_whose_new_id_cannot_be_read_still_stops_the_switched_process[fork, rewind]`.
5. **A rewind cut the log even when the copy to the trash failed.**
   - `_trash_log` answered `""` and the answer was ignored.
   - The copy came AFTER the tool had already dropped the turns (Codex `thread/revert`), so nothing was kept anywhere.
   - Fix: `_keep_log_first` makes the copy before the tool acts. If it fails, `NOT_KEPT` and nothing changes.
   - Pin: `test_a_rewind_whose_log_cannot_be_kept_changes_nothing`.
6. **Stopping the tool for a branch or rewind ended programs it served with no word** (the v1.422.0 B4 rule).
   - On a rewind, the old LiveSession and its `stopped_note` were also thrown away.
   - Fix:
     - the programs are read before the stop (`_programs_of`);
     - `_said_stopped` runs after the stop: into the original session on a branch, into the fresh LiveSession on a rewind;
     - `programs.stopped_words` gains the words `branch` and `rewind`.
   - Pin: `test_a_branch_that_stops_the_tool_names_the_program_it_served`.
7. **A tool that did not answer was a 500.**
   - When: the tool did not answer, or its process died (`TimeoutError`, `HarnessGone`). The answer read "internal error [err_…]: TimeoutError".
   - Found by accident: a scenario one turn short waited 60 s, then answered 500.
   - Fix: `NO_ANSWER`, a 409 in words. The process is stopped (as in item 4).
   - Pin: `test_a_tool_that_does_not_answer_is_said_in_words_never_a_500` (4 cases).

Checked and found right:

- The reply-end wait (`ref_save`) is bounded by `ID_SAVE_WAIT_S` (2.0 s, shielded).
  - `note_sent` never raises, and the future is always set.
  - No `send_user` waits for its turn: ACP and Pi write and return, and Codex awaits only `turn/start`. So there is no self-wait.
- Pi with two identical messages maps them by position in the tail.
- A Codex fork stops only that session's own app-server.
- Both routes are found by master's `test_wave6_dead_code_v14390._route_called` caller scan, run against this tree.
- Re-measured: the fork entry is usually an `attachment` record, not the assistant record (the `parentUuid` of the second message). `--resume H --fork-session --session-id N --resume-session-at <that attachment uuid>` sent exactly `alpha, reply 1, gamma`.

Mutations, 11, all red:

| Mutation | What it breaks |
|---|---|
| R1 | the terminal check removed |
| R2 | the filter returns everything (red only once the pin was made non-vacuous) |
| R3 | the message is noted on a failed send |
| R4 | the Pi fork is not stopped |
| R4b | the Pi rewind is not stopped |
| R5 | a failed trash copy is ignored |
| R6 | the programs are not said |
| R7 | the no-answer `except` removed |
| R8 | a branch does not copy `rewound.json` |
| B18 (builder's, re-run) | the reply's end does not wait |
| B6 (builder's, re-run) | no terminal check |

Run green:

- the lane and review files: 34;
- 16 Build › Code files: 288;
- the five vocabulary scans: 157;
- ruff (E9, F) on the touched files.

### Still OPEN after review

- A held queue (C4.8) survives a rewind and is taken back into the box beside the rewound message's words. Nothing is lost, but the box gets both.
- The message's ⋯ menu does not close on Escape or on an outside click. On a hover device its button fades while the menu stays open.
- `_running_driver` starts an idle Codex or Pi session for a branch. With the pool full, that can wait up to `POOL_WAIT_S` (15 min) inside the HTTP request.
- `_write_events` still rewrites `meta.json` in place (not temp + replace). A crash there leaves only a stale `dropped` count.
- If the daemon dies between Pi's fork and `store.update`, the row stays on the old Pi session while the log is cut. Nothing is lost: the old log is in the trash, and Pi's original file is untouched.
- Codex `thread/revert` and its rollout were not re-measured. The builder's measurement stands, and fix 2 does not depend on how the rollout looks.
