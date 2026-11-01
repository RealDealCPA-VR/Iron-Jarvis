# Chat › Code and Build audit, 2026-11-01 (v1.420.2)

North star (the owner's words): the only app they reach for, with all their
subscriptions and local models; fast, reliable, accurate, nimble, modifiable.
Plus one idea to design: in a model team, the lead and reviewers give feedback
on the work they just judged, so the worker models learn to work the way the
lead or reviewer would.

Sources: four read-only code audits of master v1.420.2 (Chat › Code harness;
Build › Code + Build › Terminal; the team and its learning loops; models,
accounts and local endpoints), each claim at file:line; the owner's live
record (read-only: coding sessions, helper runs, tool calls, team records);
spot checks of the top claims in code. Nothing was changed.

## What the live record shows (this week)

- The newest coding session ("check the UI and the user experience…",
  Opus 5.5 lead, glm-5.3-flash worker on the DGX Spark):
  - 5 of 9 replies failed. One was an expired Claude sign-in. Three were
    "/goal" messages, which v1.416.0 already fixed. One was cut off by a
    restart.
  - Today's reply has run 2+ hours. The lead handed about 14 pieces to the
    glm worker.
- The worker failed 25% of its shell commands: 182 of 742 in one day. The
  largest share were Linux commands (head, tail, grep, sed, `;`, heredocs)
  run in Windows cmd.exe. The lead's own Bash runs in Git Bash; helpers were
  left on cmd by an earlier decision (v1.386.2).
- Several worker reports say outright that they did not finish or did it
  wrong ("the final icon is wrong", "I was not able to complete the task").
  The lead re-delegates; nothing learns from it.
- The Opus reviewer could not verify the work: "This session only has read
  and search". The reviewer's tools are read-only by design.
- Team records hold 2 rows ever. Pieces handed to a worker BY NAME are never
  recorded, so routing and any learning loop see almost none of the work.
- The Autonomy card's metric misses most cards: 12 asks this week, all
  "unattributed" (9 of them were helper cards).

## Ranked gaps

P0 = blocks the north star now. Size S/M/L. "Live" = seen in the owner's record.

### A. The team does not work well yet (and cannot learn)

| # | Gap | Pri | Size | Evidence |
|---|---|---|---|---|
| A1 | Workers run commands in cmd.exe and write Linux commands: 25% fail | P0 | S | live; `harness/dialects.py:887` (only Claude's Bash has `shell_syntax="bash"`); helpers use plain `shell` |
| A2 | A piece handed to a worker by name is never recorded | P0 | S | live (2 rows); `agents/delegate_tool.py:656-663`, `model_team.py:2350` |
| A3 | Workers never see the standard they are judged by: no AGENTS.md/CLAUDE.md, no harness layer, no accepted lessons, no reviewer checklist | P0 | S | `agents/runtime.py:2525` (folder rules only on request; helpers never ask); `harness/helpers.py:255-271` |
| A4 | The reviewer cannot verify (read and search only) | P1 | S | live review text; `finish.REVIEW_TOOLS` |
| A5 | A local model's tool call written as text is recovered only in harness turns; helper/agent runs on glm just end | P1 | M | `harness/mode.text_tool_call` gated at `chat_turn.py:10122`, `routes/chat.py:5223`; none in `agents/runtime.py` |
| A6 | A review verdict is credited to every piece in the reply, and its reasons are thrown away | P1 | M | `team_record.credit_review` `:322-359`; `finish.py:3076-3079` (report kept in memory only) |
| A7 | The folder's `sub_agents/` briefs tell the lead "no helper runs in this session" | P2 | S | `harness/components.py:820-825` |
| A8 | Team use since the lead was locked (v1.403.0) is unmeasured | P1 | S | last bench: v1.400.2, before the lock |

### B. Reliability: work is lost or stuck

| # | Gap | Pri | Size | Evidence |
|---|---|---|---|---|
| B1 | A brief provider error at round 35 of a 40-minute reply ends it, deletes its saved progress, and Retry starts from scratch | P1 | M | `claude_native/transport.py:111` (CLI retries off); `router.py` ≤2 retries; `routes/build.py:2089-2091` forgets the record unless interrupted |
| B2 | Stop / Esc does not stop a running command (up to 10 min) | P1 | S | `routes/chat.py:2938-2940` says so; kill code exists in `harness/stall.py:480-503` |
| B3 | Build › Code: an Iron Jarvis restart (update, watchdog, crash) kills every CLI and nothing offers to continue | P1 | M | `hosted/process.py:228-236`, `hosted/service.py:2448-2461`, `:2727-2766` |
| B4 | Build › Code: the 15-minute idle stop silently kills dev servers the tool started; plan item C4.7 was not built | P1 | S | `hosted/service.py:110-114`, `:2436-2446` |
| B5 | Build › Code account limits: one manual press, Claude only, no waiting for the reset | P1 | M | `hosted/service.py:2598-2604`; no `auto_wait` in `hosted/` |
| B6 | New terminals default to the Store copy of PowerShell, the program behind the restore failures | P2 | S | `terminals/shells.py:22-27` |
| B7 | Build › Code holds one global lock while a CLI starts (up to 60 s); a 7th session is refused | P2 | S | `hosted/service.py:1677-1695` |

### C. Accurate and fast (parity with the native tools)

| # | Gap | Pri | Size | Evidence |
|---|---|---|---|---|
| C1 | The finish check's folder snapshot stops at 5,000 files, so in a bigger repo a reply that changed files by command skips the check; the walk also runs, serially, before every message | P1 | S/M | `harness/folder_changes.py:93-97`; `finish.py:2510-2512`, `:3849` |
| C2 | Parity is unmeasured since v1.386–387, and the bench runs a bare lane, not the product path | P1 | M | `harness_bench/runners.py:993` (`stream_chat_turn` under yolo); fix plan C7 not done |
| C3 | Build › Code waits up to 3 s for `git add -A` BEFORE the message reaches the tool | P2 | S | `hosted/service.py:2234` vs `:2241`; `hosted/panes.py:525-570` |
| C4 | An image the model reads arrives as another model's description, not the picture | P2 | S | `harness/dialects.py:395-397` → `tools/images.py:218-232` |
| C5 | Tool words promise what the harness does not do: the approval sentence, Bash's 60 s default (Claude Code: 120 s), Read cut at 32,000 chars | P2 | S | `harness/mode.py:497-529`, `sandbox/policy.py:27`, `chat_turn.py:5925` |
| C6 | The "what I did last turn" note attaches to the newest reply even if it ran no tools | P2 | S | `harness/sessions.py:965,988` |
| C7 | One `claude` process per tool round (~0.5 s each); decision D4 measured, not built | P2 | M-L | `claude_native/transport.py:384-410` |

### D. Every subscription and local model, everywhere

| # | Gap | Pri | Size | Evidence |
|---|---|---|---|---|
| D1 | A subscription pick fails over to metered API keys by default (plain chat, agents, schedules) | P1 | S | `router.py:125-128`, `:2246-2300`; `core/config.py:653` `strict_model_pin=False` |
| D2 | Codex models cannot be picked (default only); stale keyless rows (gpt-4o) can silently fall back | P1 | S/M | `agents/dynamic.py:45-55`; `hosted/service.py:105`; the parsed Codex cache in `chat_turn.refresh_codex_windows` is unused for rows |
| D3 | A local model's context window is never read from the fleet probe (dead code), so it plans at 32k unless the gateway states it | P1 | S | `chat_turn.py:7513-7519` reads `node.models` / `m.name`; models live on `NodeSnapshot`, field `id` |
| D4 | A second Iron-Proxy account runs Claude Code without the owner's MCP servers, skills, hooks, commands | P1 | M | `iron_proxy/accounts.py:117-133` (CLAUDE_CONFIG_DIR); only the CLAUDE.md block is mirrored |
| D5 | OpenCode as a chat provider runs its own tools unsupervised (no card, no ledger, no Undo) | P1 | S | `providers/adapters/opencode_cli.py:174`; Build › Code's `permission_env` not used |
| D6 | Account choice is missing in plain chat, agents, schedules and night tasks; Grok cannot be pinned | P2 | M | `harness/night.py:140-175`; `build_accounts.VENDORS` |
| D7 | Grok: 4,096-token output cap and no cut-off handling | P2 | S | `providers/adapters/grok_cli.py:71`, `:166-196` |
| D8 | Tool/vision capability is judged per gateway, not per model; Connections copy is out of date | P2 | S/M | `routes/fleet.py:760-761`; `ConnectionsPage.tsx:893` |

### E. Nimble and modifiable

| # | Gap | Pri | Size | Evidence |
|---|---|---|---|---|
| E1 | The harness's own instructions are invisible; an invalid folder setup is ignored silently (the Handbook promises a note) | P2 | S/M | `harness/mode.py:982-983`; nothing reads `route.harness` on screen |
| E2 | Chat's "use a coding session" offer opens Build › Code empty: the request is retyped | P2 | S | `tools/autoselect.py:3592` |
| E3 | Terminal → Code view can open the older conversation after `/clear`, or a second writer on one session | P2 | S | `hosted/service.py:2584`, `:526-530` |
| E4 | The real CLI's own setup is invisible in Build › Code (MCP servers and their sign-in, Codex "/" commands, compact) | P2 | S/M | `hosted/claude.py:1098-1107` |
| E5 | No per-folder model defaults; adding a provider touches ~19 files | P2 | M | `core/models.BuildFolderRecord`; grep of a provider id |
| E6 | The Autonomy metric drops helper and coding-session cards as "unattributed" | P2 | S | live; `eval/autonomy.py:~860` |

## The owner's idea: Team notes

How a lead's or reviewer's judgment flows today: it is recorded only when a
piece was asked for by ROLE (A2), and is credited reply-wide (A6). It changes
WHO gets the next piece (routing), never HOW a worker is told to work. The
library reflects only on the lead's own session and is told not to write
about the assistant; the coach ignores team records and only runs on a press.

Design, in phases, reusing what exists:

1. **Phase 0 (S, no model calls):** A2 (record named pieces), the files each
   piece's session wrote on its record, and a team-tier bench baseline (A8).
2. **Phase 1a (S, no model calls):** workers see the standard (A3): the folder
   rule files, the harness layer, the lessons the owner accepted, a "your
   reviewer will check" list and `sub_agents/<role>.md`, passed once from the
   lead's turn, the same for every piece of a reply (cache-stable, bounded).
3. **Phase 1b (M, no extra calls):** structured evidence inside calls already
   made.
   - The reviewer lists each problem as `R<n>. path:line — what and why`
     before its verdict line.
   - The lead answers each point: `R1 fixed` or `R2 does not apply: why`.
   - Plain code parses both. A finding is attributed to the worker whose
     session wrote that path. "Fixed" counts only if a later piece wrote it.
   - The findings are stored masked and scanned, never from a low-trust run.
   - The member card shows "reviewers' findings (30 days): 6 confirmed,
     1 did not apply · first-pass approval 3 of 7".
4. **Phase 2 (M, rationed calls): proposals.** When a member has at least 4
   confirmed findings across at least 2 sessions, a distiller turns them into
   at most 3 working habits, each citing the findings it generalizes.
   - It runs on the library's idle pacing, at most once a day per member, on
     the reviewer's own model (pinned, Local only respected).
   - Each habit is evidence-checked and scanned. The owner accepts or
     declines it on the existing proposal screens.
   - Accepted habits go into the member's `TEAM-NOTES.md`, injected only
     into that member's team pieces. A habit seen in two members of one role
     becomes a `sub_agents/<role>.md` edit.
5. **Phase 3 (L): measurement.** Each member's record is split at the moment
   a note was accepted: first-pass approval, confirmed findings per piece,
   check pass rate. The bench runs the team tier with notes on and off. No
   numbers are shown under 5 pieces, and a note that makes things worse is
   flagged for removal, never removed automatically.

## Proposed waves

1. **Wave 1, "A team that works" (one release, mostly S):** A1, A2, A3, A4,
   A5, D3, A7, E6, then the team-tier bench baseline. This is exactly what
   cost today's two-hour reply.
2. **Wave 2, "Never lose work":** B1, B2, B3 (the M version: Continue with the
   CLI's own resume), B4, B5, B6.
3. **Wave 3, "Team notes":** Phases 1b, 2 and 3 above.
4. **Wave 4, "Every subscription, everywhere":** D1, D2, D4, D5, D6, D7, D8.
5. **Wave 5, "Fast and accurate":** C1, C3, C4, C5, C6, B7, then C2 (re-measure
   parity on the product path), then decide C7 (D4) on the numbers.
6. **Wave 6, "Nimble and modifiable":** E1–E5.

## Decisions for the owner

- **A1:** do helpers run commands in Git Bash when it is installed, like the
  lead? This reverses the v1.386.2 choice for helpers.
- **A4:** the reviewer gets the read-only command list (git status, diff, log,
  show, ls, cat…) but not test runs, which write caches. Running tests stays
  the finish check's job.
- **D1:** failover stays within the same billing class by default
  (subscription → subscription or local); crossing to a metered API key is an
  opt-in.
- **B3:** continue cut-off Build › Code turns with the CLI's own resume now (M),
  or later move the CLIs into a separate host process that survives restarts (L).
- **Team notes:** the distiller spends plan usage on the reviewer's model, at
  most once a day per member. Should it be on by default, or a switch?
