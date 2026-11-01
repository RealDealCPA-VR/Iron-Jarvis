# Iron Jarvis fix master plan, 2026-10-31

Baseline: origin/master `5553f39f` = **v1.415.0**, which is what the live
install runs (`/health` read 2026-10-31). The checkout at
`C:\Users\VR\Projects\Iron-Jarvis` is 52 commits behind it; start from
origin/master. Nothing in this document has been changed in code.

Two questions were asked. Part A: what the product-readiness audit asked for
that is still not met. Part B: why Chat › Code (Iron Jarvis's own coding
harness) and Build › Code (the GUI over the real Claude Code / Codex / Pi /
OpenCode) feel worse than the native desktop apps, and what to do about it.
Part C is the work list, grouped and ordered, written for a coding agent.

Evidence comes from four places: the code at v1.415.0 (every `file:line` is
in that tree), the audit docs (`IRON JARVIS PRODUCT-READINESS AUDIT.md`,
`docs/PRODUCT-READINESS-RESULTS.md` §0g), the live install's own records of
the four coding sessions the user ran this week (counts and durations only),
and the plans and walkthrough in `docs/IJ-HARNESS-*.md`. Where a claim was
read but not run, the task says "confirm first".

## Standing rules for whoever builds this

- Every push bumps the version (`scripts/bump_version.py`), one builder and
  one independent reviewer per task, every guarantee mutation-checked, the
  full suites before a push. These are the repo's rules; this plan adds none.
- The two chat lanes (`daemon/chat_turn.py` + `routes/chat.py`) move in
  lock-step. The Terminals view does not change. No look-and-feel change
  beyond what a task names.
- Several causes below are recorded DESIGN decisions (the user's decisions 6
  and 7 of `docs/IJ-HARNESS-PLAN-2/3.md`, the v1.300.0 one-process-per-call
  design, "history is text only so a session can move accounts"). Tasks that
  touch one say so and propose the smallest change that keeps the decision's
  purpose. Section D lists the decisions the user must make before those
  tasks start.

---

## Part A. The product-readiness audit: what is still open

The last full re-audit (§0g, v1.388.0) scored 77/100 with 155 of 213 items
passing and 58 partial. Since then v1.389.0 to v1.415.0 shipped. Checked
against the v1.415.0 code:

**Closed since the re-audit (all from v1.389.0's "harness gaps" release):**
Chat › Code replies are listed in Running now, the orb and the home line
(`dashboard/lib/runningNow.ts:191-256`); the Chat › Code receipt shows
approvals and refit (`lib/receiptFacts.ts:58-74`); its failure line has
Retry, Sign in and Details (`components/harness/HarnessFailure.tsx`); the
"IJ harness" name is gone from primary pages (`lib/buildWords.ts:33-60`);
"Compact" became "Summarize"; the orb no longer says "a session is in
flight" (`components/MoodOrb.tsx:63`); the bell's "harness file" line is gone.

**Partly closed:** "Build session" in daemon sentences (the folder title
`WORK_FOLDER_TITLE` and five 404s in `routes/build.py` still say it); tray
notices (long Build replies now reach the tray, missions / chat turns /
helpers / media still do not); G62 remote end state (rooms handle `pending`,
the delegate tool and mission card do not).

**Still open, nothing touched since v1.388.0** (each is a task in Part C,
section C6):

| Audit item | Where it still shows |
|---|---|
| "summarize my latest emails" goes to web search; "what apps can you work with", "what are missions", "what is build for" arm nothing; chat never suggests a mission or Build | `tools/autoselect.py`, `connectors/app_names.py` (probed live against the snapshot's own matchers) |
| G54 risky website step from chat or an agent run is refused with "approve it, then try again" while nothing waits | `browser/tools.py:1336-1341`, `browser/panel.py:665-672` |
| A failed @-agent says only "couldn't answer"; an @-round has no Stop; the panel shows a raw `panelKind` word | `app/chat/page.tsx:3134-3143`, `:9906`, `agents/threads.py:1475` |
| Helper failures read raw (`crashed: ProviderError: …`) on the mission log and Build helper rows | `agents/agent_tools.py:743`, `agents/delegate_tool.py:819`, `components/harness/HarnessHelpers.tsx:119-139` |
| Chat hides Iron-Proxy's exact account sentence behind "Press Retry" | `providers/failure_words.py:753-785` vs `:808-847` |
| Key door and Connections print raw probe text | `connections/probe.py:84,98`, `components/onboarding/ConnectDoors.tsx:789-791`, `settings/pages/ConnectionsPage.tsx:1645` |
| Connections words: "Subscription & local providers", "Rescan local CLIs", "Sessions use this provider by default", "Server type", `_oauth_client_id`; Channels / SMTP / IMAP; "computer control" | `ConnectionsPage.tsx:2364,1829,2378,1263,1057,1147`, `EndpointModelPicker.tsx:65`, `lib/builtinToolWords.ts:48-60` |
| "worktree" to the user; Iron-Proxy named in accounts; "session" now with THREE meanings | `components/harness/HarnessBranch.tsx:348-509`, `HarnessAccounts.tsx:201`, `components/buildCode/BuildCodeView.tsx:1657-1714` |
| Local only says "this PC" while LAN hosts pass; "Don't ask" does not say it acts with connected apps; mission screen never says local or cloud; helper row names no machine | `lib/localOnly.ts:36-50`, `lib/permissionLevels.ts:98`, `mission/MissionOutput.tsx:395-415`, `lib/buildHelpers.ts:366` |
| Grok has no Sign in press | `components/connections/CliSignInPress.tsx` (no grok) |
| The orb says Working while a session is parked on the user | `components/MoodOrb.tsx:76-82` |
| Session board's red "Failed" lane for stopped runs; "Attaching to sessions…"; "this action has no safe inverse"; nav Sentinels / Reflexes / Artifacts / "Local fleet" (owner's call) | `lib/kanban.ts:54`, `app/terminals/page.tsx:1213`, `TurnReceipt.tsx:1439`, `lib/nav.ts:277-480` |
| Fleet's model-server form has no key field; Gemini not offered as free; no import offered at first run | `app/fleet/page.tsx:846-870`, `ConnectDoors.tsx:104` |

**New since the re-audit, in Build › Code (it did not exist then):**

| Gap | Where |
|---|---|
| Failure words blame Iron Jarvis and say "Press Retry", but the view has no Retry press. The same dead end v1.389.0 fixed in Chat › Code | `hosted/words.py:346-360` (wraps the tool's error in a RuntimeError, so `failure_words` finds no provider), `components/buildCode/CodeConversation.tsx:208-233` |
| The per-reply receipt is "N in · M out [· cost]" only: no model, account, place, duration or changed files on the reply | `lib/buildApp.ts:1335-1360`, `CodeConversation.tsx:235-249` |
| No Local only and no trust posture (by design; only the Memory box warns) | `hosted/jarvis_tools.py` docstring |
| Engine-room words: "MCP servers and plugins", raw MCP tool ids on cards, "Code-view session", "The harness" fallback label, Codex's "cannot run commands in its sandbox", "Iron-Proxy" | `hosted/words.py:85-87,133-146,176-184,220,258`, `hosted/codex.py:94-97,684`, `CodePanes.tsx:403` |
| Two different things are both called "Code"; Running now labels both "Coding session"; VOCABULARY.md has no row for Build › Code; `BUILD_BLURB` still says Build is terminals only; no "code" search alias for Build | `lib/buildWords.ts:33-62`, `lib/runningNow.ts:206-237`, `lib/nav.ts:124-133` |
| Build › Code asks publish no `interrupt.asked`/`answered`, so the autonomy metrics read 0 interruptions for Build while the live record holds 40 cards in one session | `hosted/service.py` (no `InterruptNote`), `eval/autonomy.py` |

Verdict on Part A: the audit's remaining items are mostly words and small
wirings, as the audit itself said. The one structural remainder (G54, the
website approval that does not resume) and the discoverability phrasings are
the only ones that need more than a sentence change. None of them is the
reason the Code views feel worse than the native apps; that is Part B.

---

## Part B. Why the two Code views feel worse than the native apps

Six root causes, in order of how much they cost the user. Each is grounded in
code and in the live sessions.

### B1. Approvals ask for things the native apps do not, and "Allow for this session" means different things in the two views

Live: one Build › Code session in "Auto edits" showed **40 approval cards** in
about an hour; the user answered "Allow for this session" 24 times and the
cards kept coming, because Claude Code's `permission_suggestions` are
per-command-prefix rules (`hosted/claude.py:236-275`), while the same words
in Chat › Code grant the whole command family (`harness/session_grants.py`).
In Chat › Code under the DEFAULT posture ("Ask first", edits free) a plain
"read 2, edit 2, run tests, commit" task costs 2 cards best case and 4 to 7
with Enter, versus 2 in Claude Code's Accept edits, because: every Bash call
is a card (no read-only command list, `core/config.py:99`); the finish check
re-runs the test as a NEW shell call (`harness/finish.py:2822-2867`); project
checkers card in EVERY posture including "Don't ask" (`harness/checkers.py:
553-565`, `routes/chat.py:5356-5357,5592-5593`); the git guard is click-only
everywhere (`harness/git_guard.py:652-671`); and two residual holes of the
v1.404.1 one-approval fix mean a grant given mid-turn does not cover the
same turn's `process_start`, finish check or checkers (`routes/chat.py:6010`
vs `:6026`, `session_grants.py:197-219`). Under Auto, each undecided command
is a serial model call of up to 6 s (`core/auto_approve.py:975-977`,
`routes/chat.py:5469-5487`), and the Auto posture silently falls back to Ask
first for an approved plan's act turn and every resume (`auto_approve.py:
143-156`). The labels disagree too: Chat's "Ask first" lets edits through,
Build's "Ask first" is Claude Code `default`, which asks before edits.

### B2. Chat › Code's conversation is rendered and settled the wrong way

The 3,456-line `HarnessSession.tsx` re-renders on every streamed frame
(`lib/useChatStream.ts:1805` `setLastEventAt` per frame; `HarnessSession.tsx:
478`), walking every message, TurnReceipt (1,535 lines, not memoised), the
panes and inline lambdas each time; the idle view re-renders every 4 s from
the session-list poll (`IjHarnessView.tsx:74,230,268`). The live reply is
re-parsed as Markdown over its whole length each frame (`HarnessSession.tsx:
319`) and does not follow-scroll while text grows (`:2267-2270`). At the end
of a turn `setSending(false)` drops the live block BEFORE the saved reply is
read back (up to 4 × 500 ms), so the answer blinks out and comes back in a
different shape (`:1228-1233`, `:968-973`), followed by 7 to 9 more requests
whose results pop in one by one. Steps and prose never interleave (`WorkLine.
tsx:810-825`, `:1004-1116`); the same fact is repeated under one reply by up
to six chrome lines (Worked for, Checked, plan, ReplyChanges, TurnReceipt,
Next step). The finish check's checklist streams live and is then removed
from the saved reply (`finish.py:3330-3341`), so the reply rewrites itself.
Build › Code already renders better (memoised interleaved items,
`components/buildCode/CodeConversation.tsx:89,135,254`).

### B3. Chat › Code pays fixed costs per round and per message that the native apps do not

By design (v1.300.0) every model call is a NEW `claude` process with a new
temp dir, a new loopback relay with a new TLS connection, a new inert MCP
child, the whole history replayed and acknowledged frame by frame, and the
answer released only after the process EXITS (`providers/adapters/claude_
native/transport.py:341-593,1133-1192`, `admission.py:428-437`, `inert_mcp.
py:11-13`): roughly 1 to 3 s of dead time per round, growing with history
(estimate, not measured). A follow-up message is rebuilt from the user's and
assistant's TEXT only, so the model forgets what it read, edited and ran in
the previous turn and re-reads (`harness/sessions.py:866-901`; the comment at
`routes/build.py:2958-2967` says this is so a session can move between
accounts). Every editing reply gets a second test run plus one extra model
round for the finish check (decision 6), project checkers of up to 60 s per
editing round (decision 7, `checkers.py:60-66,102`), and possibly a
closing-offer round (`closing_offer.py:66-74`): three to four extra model
calls and two extra command runs on a reply Claude Code would end at once.
Before the first token: grounding hops with no soft deadline (`chat_turn.py:
8033`), a project-map walk EVERY turn (`project_map.py:84`), a second full
folder walk for the finish check (`finish.py:3670`), and up to 10 s waiting
for packs after boot. Per round: two full-transcript rewrites awaited on one
writer thread (`reply_record.py:217-338`, `SAVE_WAIT_S` 10), per-call intent
and ledger commits competing with 4 MB rows, and TurnLog re-serialising the
merged text frame on every token (`durable_turns.py:304-316`). Codex is
worse: an unknown window is planned as 32k (`context/budget.py:71`), so
history is dropped early and re-summarised; one tool call per round; reads
go through the command tool and card.

### B4. Build › Code shows less than the terminal and hides what the tool is doing

Plan mode never shows the plan: `ExitPlanMode` becomes an approval titled
"Show the plan" whose event carries no `input.plan` (`hosted/claude.py:
207-208,708-725`). Deny always sends the fixed text "The user said no to this."
and the UI never sends a reason, so "no, do X instead" is two steps
(`claude.py:563-564`, `CodeAskCard.tsx:115-123`). Subagent (Task) work is
flattened into the main thread (`parent_tool_use_id` never read). Hook
output, "compacting…" and every control request other than `can_use_tool`
are dropped (`claude.py:687-692,739-756`). Thinking is always rendered
finished (`CodeConversation.tsx:178-183`). A long Write/Edit shows nothing
until it completes (`input_json_delta` ignored, `claude.py:758-779`) and
command output does not stream (Codex `aggregatedOutput` at the end,
`codex.py:654-664`). Input is cut at 300, output at 1,500 characters with no
way to expand (`hosted/events.py:63-65`; live: 302 of 1,868 tool outputs in
one session hit the cap); the command title is the raw command cut at 90
although Claude Code's Bash input carries a `description` field that is never
used (`claude.py:177-182`). In Full access, edit events arrive without
`diff`/`line` (live: 0 of 269 edits; in Auto edits 37 of 37 had them), so the
mode that runs fastest shows the least. The view keeps the newest 2,000
events and the user's earlier messages vanish (`service.py:108,1041-1044`;
live: a 5,152-event session showed one user message); a disk or terminal
session opens with no history (`BuildCodeView.tsx:1033-1036`). Cold starts
(first message, after 15 min idle, after an effort change, when a 7th
process evicts one) show the same spinner with no word (`service.py:100,
1116-1141,1244-1249,1724,1894-1902`). Each reply's end waits up to 10 s for
the id save and up to 15 s for an unqueued note (`service.py:106,1447-1455`,
`claude.py:131,942-960`). Esc and Ctrl+C do not interrupt, there is no
Shift+Tab, `!` or `#`, the `/` menu is thin and empty for Codex, model and
effort chips are disabled while a reply runs, and a message mid-reply is a
note, never a queue (`BuildCodeView.tsx:591-593,811-890,1195,1221`,
`service.py:1650-1651,765-771`). Failures end in "Press Retry" with no Retry.
Every GET of a session re-reads and re-parses its whole `events.jsonl`
(`events.py:288-308`).

### B5. Starting and finding work costs more presses than it should

Chat › Code: the empty state has no composer and no button (`IjHarnessView.
tsx:632-646`); a first reply in a chosen folder is six or seven presses and
two spinners, because New session discards the view its own POST returned
and Choose… then moves the session with a second POST and a DELETE
(`IjHarnessView.tsx:384-397`, `HarnessSession.tsx:1695-1754`); the textarea
has no autofocus and is a fixed two-line box (`:2489`, `:3235`); drafts are
lost on every session switch (`:1269`); archiving is a dead end with no
archived list and no Unarchive press (`HarnessSession.tsx:1477-1478`,
`lib/buildSessions.ts:623` unused); keyboard shortcuts stop working once
the list (portaled into the app sidebar) has focus (`lib/harnessKeys.ts:
83,132-145`); titles are the first 48 characters of the question
(`harness/sessions.py:602-605`); the Terminals group takes 40 % of the list
and leaves the view. Build › Code: no rename, no delete, no search in the
UI although the routes exist (`BuildCodeView.tsx:990-1014,1566-1589`).

### B6. There are two "Code" products with two sets of everything

Chat | Code is Iron Jarvis's harness; Build's </> Code | Terminal is the real
CLI. They have different lists, start flows, approval cards, mode names
("Accept edits / Don't ask" vs "Auto edits / Full access" vs Claude Code's
"Accept edits / Bypass permissions"), receipts, failure lines and keyboard
rules; Running now calls both "Coding session". A user who learns one is
surprised by the other. Build › Code's start composer and conversation
renderer are the better of the two and Chat › Code's receipt, Retry line,
Local only and grants are the better of the two.

---

## Part C. The work list

Each task: **id · title · effort (S/M/L)**, then problem → fix → done when.
"Lock-step" means both chat lanes. "Decision" points to Section D.

### C1. Approvals: one approval means one approval (P0)

**C1.1 Build › Code: "Allow for this session" covers the command family, as in Chat › Code · M**
Problem: Claude Code's suggestion is a per-prefix rule (`hosted/claude.py:
236-275`); 24 presses did not stop the cards (live). Fix: when the card's
kind is `command` and the mode is not `full`, offer a third answer "Allow
all commands this session" that answers allow with Iron Jarvis's OWN
`updatedPermissions` rule for the whole Bash tool (destination session),
beside Claude Code's narrower suggestion; keep Claude's rule as "Allow
similar commands". For Codex, map it to `acceptForSession` when offered, else
keep the card once-only and say so. Same vocabulary as Chat › Code's chip
(`lib/approvalWords`). Done when: a recorded session with N command cards
answered "all commands" on the first shows zero further command cards in that
process; the fixture `build-app-events-v1409.json` gains the case; the
contract test on both sides.

**C1.2 Chat › Code: close the two v1.404.1 residuals · S**
Problem: a "conversation" answer on the lead's own shell card widens
`card_grants` only (`routes/chat.py:6026-6028`) while the posture reads
`armed_grant` (`:5360-5364`, set at `:6010`), so `process_start` cards again
in the same turn; a helper's session grant never reaches the lead's RUNNING
turn (`session_grants.py:197-219`). Fix: widen `armed_grant` by the same
`COMMAND_FAMILY` at answer time; have `release_helpers` also update the
parent turn's armed set (a per-turn reference the lane re-reads before each
card decision). Done when: a test drives shell → allow-for-session →
process_start in ONE turn with no second card, and a helper's grant releases
the lead's waiting finish-check card.

**C1.3 Finish check and checkers never card on their own under a session grant, and never under Don't ask · M** (Decision D1)
Problem: `needs_a_person` cards the check's test re-run and every checker in
every posture including yolo (`finish.py:2274-2288`, `checkers.py:553-565`,
`routes/chat.py:5356-5357,5592-5596`); the card reads "The assistant wants
to run…" although Iron Jarvis scheduled it (`ApprovalCard.tsx:255-261`).
Fix: (a) under "Don't ask" and under a session grant on the carrier tool,
run both without a card (the user's own command, re-run by Iron Jarvis, is
not a new risk); (b) when a card is still needed, title it "Iron Jarvis wants
to re-run the tests to check the work" with ONE button "Allow checks this
session"; (c) skip the re-run when the last test command already passed
AFTER the last edit (`_test_calls`, `finish.py:2789-2829`). Done when: the
walkthrough's bug-fix task under Ask first shows one command card, not two;
checkers under Don't ask show none; a test pins the skip.

**C1.4 A read-only command list under Ask first · S**
Problem: every `git status`, `ls`, `cat`, `rg` through Bash is a card
(`core/config.py:99`). Fix: reuse `core/auto_approve.is_test_command`'s
shell reading to add a `READ_ONLY_COMMANDS` pass rule (git status/diff/log/
show, ls, cat, rg, grep, find without -exec/-delete, head, tail, wc, pwd,
which, node/python --version) applied under Ask first and Accept edits only,
never to a command with a redirect, `;`, `&&`, `|` into a non-read program,
or an unquoted `$(`. Done when: the typical task under Ask first with Enter
is 2 cards (tests, commit) and a mutation that lets `rm` through goes red.

**C1.5 Auto's judge off the critical path · M**
Problem: each undecided command is a serial model call up to 6 s, 12 per
turn, seeing only the newest user message (`auto_approve.py:975-977,
1158-1180`; `routes/chat.py:5469-5487`). Fix: run the pre-pass judgements
concurrently (one gather), lower the timeout to 3 s, give the judge the
turn's first user message and the last assistant text as well, and under
Auto let the C1.4 read-only list pass without a judge. Done when: a round
with 4 undecided commands waits ≤ 3 s total in a fake-router test; the
judge prompt test shows the request text present.

**C1.6 Accept edits and Auto survive a resume, an approved plan's act turn and an account move · S** (Decision D2)
Problem: `unwatched_mode` maps them to approve_for_me for every turn Iron
Jarvis starts itself (`auto_approve.py:143-156`, `routes/build.py:2221,
3027-3030`); "Don't ask" does not fall back, so the strictest easing is the
only one kept. Fix: keep the fallback for night/CI/restart resumes (nobody
is watching), but NOT for the approved plan's act turn and Resume-when-ready
pressed by a person (the press is the watch). Done when: a plan approved
under Auto runs its act turn under Auto; a night task still falls back.

**C1.7 Cards say why, Enter is predictable, buttons do what they say · S**
Problem: under Accept edits/Auto the card never says which rule kept it
(rule words reach only the receipt, `auto_approve.py:1093-1114`); Enter is
silent on once-only cards (`DockAsk.tsx:219-220`) and on ANY no-session card
in Build › Code (`lib/buildApp.ts:1261-1264`), unlike Chat; priced cards show
"Allow for this session" but the daemon treats it as once (`ApprovalCard.
tsx:445-456`, `routes/chat.py:5938-5941`). Fix: one reason line on the card
(`approval` frame gains `why`); Enter = Allow once on every card that is not
a safety once-only (align Build with Chat); hide the session button where the
daemon ignores it. Done when: the approval fixture carries `why`; Enter
behaviour pinned equal in both views.

**C1.8 One posture vocabulary across Chat › Code, Build › Code and Claude Code · S** (Decision D3)
Problem: see B1 last sentence and `lib/permissionLevels.ts:76-99` vs
`hosted/claude.py:88-112`. Fix: name the levels by what they do, the same
in both views: "Ask before edits and commands", "Ask before commands"
(edits free), "Plan", "Auto (its own safety check)", "Don't ask". Build's
`about` lines already exist (`MODE_ABOUT`); Chat gets the same lines. Fix
"Don't ask"'s sentence to name connected apps, commits and pushes. Done
when: `permissionLevels.ts` and `hosted/*.MODE_ABOUT` are pinned to one
table; the vocabulary scan has the rows.

**C1.9 Codex on Windows: an Ask posture that does not ask for every file change · M** (confirm first)
Problem: only `untrusted + danger-full-access` or full are offered
(`hosted/codex.py:84-98`). Fix: measure whether `on-request + danger-full-
access` with Iron Jarvis answering `item/fileChange/requestApproval` can
give "edits free, commands ask"; if not, document it on the chip and keep
C1.1's "all commands" answer. Done when: the measurement is written into the
module docstring and the hidden-mode reason is in plain words.

**C1.10 OpenCode: never override a user's own allow rule · S**
Problem: `"*": "ask"` lands after the user's rules (`hosted/opencode.py:
436-460`); `task` asks; a mode change drops earlier "always" answers
(`:1010-1021`). Fix: name only the tools the user left unset; grant `task`
like the harness grants helpers; carry "always" answers across the restart
by re-sending them as session rules. Done when: the merge-order test shows
a user `"bash": "allow"` kept.

**C1.11 Build › Code asks reach the autonomy metrics · S**
Problem: hosted cards publish no `interrupt.asked`/`answered` (the
HandsOffCard reads 0 interruptions for Build). Fix: `HostedService._ask` /
`answer` publish `core/approvals.InterruptNote` with surface `build_app`,
kind card|question, actor app|phone. Done when: a recorded session's 40 cards
read as 40 interruptions in `eval/autonomy.py`.

### C2. Chat › Code: the conversation (P0)

**C2.1 Stop the per-frame re-render of the whole session · M**
Problem: B2 first sentences (`useChatStream.ts:1805`, `HarnessSession.tsx:
478,2430-2453,2714-2936`; `IjHarnessView.tsx:230`). Fix: move `lastEventAt`
and the live-step list into the hook's store (read by `TurnClock` and the
live block only, as the chat page does since v1.250.0); `React.memo` the
transcript rows, HarnessPanes and the panes' props (stable callbacks via
refs); compare the session list before `setFolders`; memoise CodeView's
terminal API. Done when: a test counts renders of a marker transcript row
while 60 tokens stream (≤ 1, like `code-split-live-v1406` does for the view)
and over three 4 s polls with an unchanged list (0).

**C2.2 The reply never blinks: keep the live block until the saved reply lands, and follow-scroll · S**
Problem: `HarnessSession.tsx:1228-1233,968-973,2267-2270,311-331`. Fix:
`busy` stays true until `settle` has adopted the saved reply (or failed);
the live text store publishes a "grew" tick the scroll effect subscribes to
(scroll only when the user is already at the bottom). Done when: a test
holds the settle GET 400 ms and asserts the live text is on screen
throughout; a stream of 50 tokens ends with the sentinel in view.

**C2.3 Interleave steps with the text, the way Build › Code already does · M**
Problem: steps sit above all text live and fold into one line when settled
(`WorkLine.tsx:810-825,1004-1116`). Fix: render the reply as the ordered list
of [text block, step rows, text block…] that the frames already carry (the
daemon emits tokens and tool_call frames in order; the saved `steps` carry
`start` offsets since v1.390.0); keep the one-line "Worked for" as the
collapsed state of each run of steps, not of the whole reply. Reuse
`components/buildCode/CodeConversation.tsx`'s item renderer rather than a
second one. Done when: a recorded reply with text→read→text→edit renders
four items in order; the e2e `ij-harness.spec.ts` step "see the test" is
one press.

**C2.4 Hide the finish check's bookkeeping from the live view · S**
Problem: the "N. met:" checklist streams live and is removed on save
(`routes/chat.py:5139-5143,6579-6583`, `finish.py:3330-3341`); the check's
test step is an ordinary row (`called_as` never read on the dashboard). Fix:
tag the finish round's frames (`check: finish` already exists on tool_call
frames since v1.390.0; add it to the round and token frames) and render them
as the single quiet "Checked: tests passed · 2 of 2 points met" line while
live, exactly as settled. Done when: the live DOM never contains "met:" and
the Checked line appears before `done`.

**C2.5 One receipt line per reply, the rest folded · S**
Problem: up to six chrome lines say the same thing (B2). Fix: one collapsed
line under the reply = Worked for · Checked · N files changed · answered by
X; the plan, ReplyChanges diff list, TurnReceipt detail and Next-step
buttons open from it. Keep every test id. Done when: the settled reply has
one `data-testid="reply-line"` and the e2e counts it.

**C2.6 Start at the composer, like Build › Code · M**
Problem: B5 first half (`IjHarnessView.tsx:384-397,632-646`, `HarnessSession.
tsx:1695-1754,2489`). Fix: reuse `components/buildCode/StartComposer.tsx`
(folder chip, model, posture, branch switch) for Chat › Code; the empty state
IS the composer with the caret in it; Send creates the session WITH the
folder in one POST (no move/DELETE); use the create route's returned view
(no second GET). Done when: the walkthrough's start step is "type, Enter";
a test counts POST/DELETE/GET on a first message (1/0/0).

**C2.7 Drafts kept per session, autofocus, a growing box · S**
`HarnessSession.tsx:1269,3235`; reuse `lib/chatDrafts` keyed by session id;
`rows` grows to 8 like the chat page. Done when: switching sessions and back
restores the words; the e2e types four lines and the box grows.

**C2.8 Archive is reversible and listed · S**
Problem: no archived list, `unarchiveSession` unused (`lib/buildSessions.ts:
623`, `HarnessSession.tsx:1477-1478`). Fix: "Archived (N)" fold in the
sidebar like Build › Code's; "Bring back" on the row and on the archived
banner. Done when: the e2e archives and brings back a session.

**C2.9 Keyboard works from the list, titles are real · S**
Problem: `lib/harnessKeys.ts:83,132-145` (portaled list outside the root);
titles = first 48 chars (`harness/sessions.py:602-605`). Fix: the key owner
includes the portaled list; ↑/↓ moves between rows; after the first reply
the daemon writes a 3-6 word title from the reply's own first line
(deterministic, no model call) when the question was long. Done when: tests
for both.

**C2.10 Panes: live Changes, a useful default, collapsible · M**
Problem: Changes re-reads only at turn end (`HarnessPanes.tsx:448-467`);
default tab Plan reads "No plan yet" (`:567,125-131`); fixed 320/400 px, no
collapse (`HarnessSession.tsx:3447`). Fix: Changes re-reads every 5 s while a
turn runs (as CodePanes does); default tab = Changes when the reply changed
files else Session; a collapse button and a remembered width (two sizes is
enough). Done when: tests for the poll gate, the default, the collapse.

**C2.11 Reduce the row of nine controls · S**
Problem: `HarnessSession.tsx:3282-3421` (Plan twice, account twice). Fix:
Plan lives only inside the posture chip; the account only in the model chip;
`window.confirm` for Undo replaced by the two-press ConfirmButton. Done when:
the one-row fit e2e still passes at 1024 px with the demo model name.

### C3. Chat › Code: the daemon's turn (P1; the biggest time wins)

**C3.1 Measure first, then cut the per-round fixed cost · M, measurement first** (Decision D4)
Problem: B3 first sentence (one `claude` process per model call,
`claude_native/transport.py:341-593,1133-1192`; TLS per call, `admission.py:
428-437`). Fix, in order of return: (a) trace one real 6-round reply on the
user's PC and write down ms per phase (spawn, MCP init, history ack, model,
exit, teardown); (b) release the answer frame as soon as `result` arrives
and run teardown in a background task; (c) keep ONE relay listener and one
`ssl` context per daemon process with HTTP keep-alive to the upstream; (d)
write the history frames without waiting for each zero-turn `result` if the
CLI accepts a burst (measure); (e) only if (a) shows the spawn dominating:
a long-lived `claude -p --input-format stream-json` process per session
with `--max-turns` per message, like `hosted/claude.py` already runs. Done
when: the trace is in `docs/IJ-HARNESS-PLAN-3.md` with before/after numbers;
(b) and (c) pinned.

**C3.2 A follow-up turn remembers the previous turn's tool work · M** (Decision D5)
Problem: `harness/sessions.py:866-901` keeps text only; comment at
`routes/build.py:2958-2967`. Fix: keep the mobility (no native carrier) but
add a compact, deterministic "what I did last turn" block from the saved
steps (files read, edited, commands run with their exit status, ≤ 1,500
chars, newest turn only) into the history as an assistant-side note, the
same shape the agent runtime's `prior_results_block` uses. Done when: a
replayed two-turn session shows the model the block and a scripted model's
second turn makes no re-read (bench: `hard-explain-trace` round count).

**C3.3 The finish check and checkers cost less · M** (Decision D1)
Problem: B3 (a second test run + one model round on every editing reply;
checkers up to 60 s per editing round, tsc over the whole project). Fix:
skip the re-run when the model's last test run already passed after the
last edit (C1.3c); run checkers once at the END of the reply (not per round)
and only over changed files, in the background of the finish round, with a
15 s budget; tsc only when the changed files are TypeScript and the project
is small (measure a cap). Done when: a reply with "edit, test, pass" makes
no second test run; a 3-round editing reply runs ruff once.

**C3.4 Grounding and preparation with deadlines, and a cached project map · S**
Problem: no soft deadline on the grounding gather (`chat_turn.py:8033`); the
project map is walked every turn (`project_map.py:84`); the finish check
walks the folder a second time (`finish.py:3670`). Fix: 2.5 s soft deadline
on the gather (slow hops report "not read in time" in the receipt, like the
fabric's own budget); cache the project map per (folder, newest mtime of
the top two levels) for the session; the finish check reuses `folder_
changes`'s first reading. Done when: a turn with a stalled fabric hop starts
in < 3 s; the second turn in a session makes no walk.

**C3.5 Codex: a real window, fewer re-summaries · S** (confirm first)
Problem: `_ladder_window_source` has no catalog rung for codex-cli, so
`DEFAULT_WINDOW` 32k plans the history (`chat_turn.py:7184-7254`,
`context/budget.py:71,338`); the compaction cache keys on an exact prefix.
Fix: a `codex` rung from the model id (gpt-6 family 400k, documented), and
a rung for the fleet node's `max_input_tokens`; confirm the re-summary loop
live on a session past 29k estimated tokens and, if real, key the cache on
the covered TURN count. Done when: `test_claude_context_window` gains a Codex
case; the live check is written down.

**C3.6 Cheaper per-round writes · S**
Problem: `reply_record` rewrites the whole tail twice per round, awaited
(`reply_record.py:217-338`); TurnLog re-serialises the merged text per token
(`durable_turns.py:304-316`). Fix: one save per round (before tools run),
appending only the new round when the row exists; TurnLog keeps the live
text as a list of chunks and joins on persist. Done when: a 40-round reply's
writes are counted (≤ 41) and the per-token path makes no `json.loads`.

**C3.7 Prompt order for the cache · S**
Problem: query-dependent sections sit before the stable harness block
(`routes/chat.py:3702-3706` vs `:3956-3957`). Fix: stable blocks first
(persona, env, harness tree, tools), query-dependent last; keep the
`_Grounding` order pin by moving it to the new order in the same change.
Done when: the golden fixture moves once and the relay's cache-read share
on round 1 of the second message is measured higher.

**C3.8 Drop chat-only prompt blocks from a coding turn · S**
Problem: `DRAFT_BLOCK`, `CHART_BLOCK`, saved workflows and the browser
section ride a harness turn (`routes/chat.py:3661-3664`, `chat_turn.py:
7994-8000`). Fix: `text_only_system`-style drop list for harness turns.
Done when: the harness golden shrinks by those sections only.

### C4. Build › Code: show what the tool shows (P1)

**C4.1 Plan mode shows the plan; Deny carries a reason · S**
`hosted/claude.py:207-208,563-564,708-725`; `CodeAskCard.tsx:55-127`. Fix:
the `approval` event gains `plan` (the `ExitPlanMode` input's plan, ≤ 20k)
rendered as Markdown; a "No, tell it what to do instead" answer opens the
box and sends the words as the deny `message`. Done when: fixture case +
a fake-harness test that reads the message back in the control_response.

**C4.2 Streaming tool rows: input as it is written, output as it arrives, subagents nested · M**
`hosted/claude.py:758-779,787-851`; `hosted/codex.py:577-664`. Fix: emit the
`tool` running row from `content_block_start`, update its `input` from
`input_json_delta` (throttled to 4/s); read `parent_tool_use_id` and nest a
Task's rows under a collapsible row; for Codex read the command output delta
notification if the schema has one (confirm with `generate-json-schema`).
Done when: fixture gains a Task case with nested rows; the contract test
decodes `parent`.

**C4.3 Thinking live; hooks and system messages as notices · S**
`CodeConversation.tsx:178-183`; `hosted/claude.py:687-692,739-756`. Fix: pass
`live`/`seconds` to ThinkingDisclosure; map every `system` subtype to a
`notice` with Claude's own words (hook output, compaction progress, status).
Done when: a recorded hook run renders a notice row.

**C4.4 Expandable input, output and command titles with the tool's own description · S**
`hosted/events.py:63-65`; `hosted/claude.py:177-182`. Fix: store `output`
up to 20k in the log and send the first 1,500 with `more: true`; a row press
fetches the full text (`GET …/events/{item}`); the command title uses the
Bash input's `description` when present, with the command underneath. Done
when: a 5k-char output expands; the title fixture case.

**C4.5 Diffs in every mode · S** (confirm first)
Live: 0 of 269 edit events in Full access carried `diff`/`line`, 37 of 37
in Auto edits. Likely cause: `tool_use_result` meta missing on those frames
(`claude.py:825-851`). Fix: measure the frame shape under
`bypassPermissions` + `--allow-dangerously-skip-permissions`; if the meta is
absent, build the diff from the Edit input (`_edit_diff` already does) and
the file's bytes after the write. Done when: a recorded full-mode session
shows a diff on every edit.

**C4.6 History that stays: load older, and import the tool's own transcript · M**
`service.py:108,1041-1044`; `BuildCodeView.tsx:1033-1036`; no transcript
reader in `hosted/`. Fix: page the events (`?before=`), "Load earlier" at
the top, and say "N earlier steps" instead of nothing; read a Claude Code
session jsonl / Codex rollout / Pi session / OpenCode DB (the `history/`
reader already parses Claude and Codex) into the view's events when a disk
session opens or after Open in terminal. Done when: a 5,000-event session
shows its first message after one press; a disk session opens with its
transcript.

**C4.7 Cold starts and holds are said, and shorter · S**
`service.py:100,106,1116-1141,1244-1249,1447-1455,1724,1894-1902`; `claude.
py:131,942-960`. Fix: a `notice` "Starting Claude Code…" / "Resuming after
15 minutes idle" when a process starts for a message; the id-save wait
becomes a background write with a 2 s cap; HOLD_FOR_NOTE_S 15 → 5; the idle
reaper 15 → 60 min for the session in front. Done when: the fixture gains
the notice; the end-of-turn latency test.

**C4.8 Keyboard and queue parity · M**
`BuildCodeView.tsx:591-593,811-890,1195,1221`; `service.py:1650-1651`. Fix:
Esc interrupts a running reply (Stop) when no card is open; Shift+Tab
cycles the mode chip; a message sent while running is QUEUED (list under the
box, as the chat page does) and sent after the turn; the `/` menu opens on
`/` at the start of any line with up to 20 rows and argument hints; Codex's
`/` list from its prompts; model and effort chips enabled while running for
tools that take a live change. Done when: e2e keys case; the queue test.

**C4.9 Failures with a press, and a real receipt · S**
`hosted/words.py:346-360`; `CodeConversation.tsx:208-249`; `lib/buildApp.ts:
1335-1360`. Fix: `failure_words` gets the tool's provider from the hosted
error (name the tool, never Iron Jarvis); a Retry press on the newest failed
reply (resend the message); the reply line reads "answered by <model> on
<account> · 2 m 14 s · 7 actions · 3 files changed", folded like Chat's.
Done when: the failure fixture reads "Claude Code couldn't finish…" with
Retry; the receipt test.

**C4.10 Sessions: rename, delete, search, one-press resume list · S**
`BuildCodeView.tsx:990-1014,1566-1589`. Routes exist. Done when: the e2e.

**C4.11 One read per view, not the whole log · S**
`hosted/events.py:288-308` (whole-file read and parse per GET). Fix: keep an
in-memory tail per live session and serve from it; read the file only when
nothing is live. Done when: a test counts file opens over 10 polls (≤ 1).

**C4.12 Full-mode edits as diffs in the Changes pane with per-file revert · M**
`hosted/panes.py:53-73,95`. Fix: when the folder is a repository, offer
"Revert this file" (`git checkout -- <file>` for tracked, delete for
untracked, two presses) and show changes per TURN (the git base = the commit
or tree at the turn's start). Done when: a recorded session reverts one file.

### C5. One Code, one vocabulary (P1)

**C5.1 Decide the names and say them once · S** (Decision D6)
Two views both called "Code" (`lib/buildWords.ts:33-62`, `app/terminals/
page.tsx:128`); Running now labels both "Coding session". Proposed: Chat ›
Code = **"Code (Jarvis)"**, Build › Code = **"Code (Claude Code / Codex /
Pi / OpenCode)"** named by the tool, Running now says "Coding session (Jarvis)"
vs "Coding session (Claude Code)"; VOCABULARY.md gains the row; `BUILD_BLURB`
and Build's search aliases updated. Done when: the vocabulary scan and
`nav.test` pass with the new words.

**C5.2 Retire the engine-room words the audit listed · S**
"worktree" (`HarnessBranch.tsx:348-509`, `HarnessSession.tsx:2564,2668`,
`HarnessStartFolder.tsx:114`, `lib/buildGit.ts:119`) → "its own copy of the
folder"; "Build session" (`routes/build.py:2277,2745,2779,3235,3816,3993`);
"Iron-Proxy" → "your accounts" (`HarnessAccounts.tsx:201`, `lib/buildAccounts.
ts:340,406`, `CodePanes.tsx:403`, `hosted/words.py:176-178`); "MCP servers and
plugins", "Code-view session", "The harness", "sandbox" (`hosted/words.py:
85-87,133-146,182-184,220`, `hosted/codex.py:94-97`); raw MCP ids on cards →
"Use <app> › <action>" through the connectors catalog's names; "this action
has no safe inverse" → "can't be undone"; "Attaching to sessions…" →
"Opening your terminals…". Done when: the scan's retired list has them and
`PENDING` is empty.

**C5.3 The same mode names and the same card in both Code views · S**
Depends on C1.8. `CodeAskCard` and `ApprovalCard` share `lib/approvalWords`
and one layout (command + one line; diff for edits; plan for plan mode).
Done when: a shared fixture renders both.

**C5.4 Re-drive the walkthrough and write the numbers · S**
`docs/IJ-HARNESS-WALKTHROUGH.md` predates the finish check, checkers, Auto,
guarded edits and command files. Re-run the three tasks in BOTH Code views
after C1-C4 with the same counting rules; add a fourth row for Build › Code.
Done when: the doc has the new table and "no more presses than Claude Code
in the same mode" is true or the gap is named.

### C6. The rest of the audit (P1/P2, independent of the harness work)

| Id | Task | Effort | Where |
|---|---|---|---|
| C6.1 | Matcher: "summarize/check/read my latest emails" → the connected mail app (or its Connect door), never web search; "what apps can you work with", "what are missions", "what is build for" arm the Guide; chat offers a mission or Build › Code for a task that names a repository or "build me" | M | `tools/autoselect.py`, `connectors/app_names.py`, `tests/test_matcher_tables_v13610.py` (rows both ways) |
| C6.2 | G54: a risky website step from chat or an agent run files a card where the user is (chat: the DockAsk; agent: the run's ask) and the approved call resumes, instead of "approve it, then try again" | M | `browser/tools.py:1336-1341`, `browser/panel.py:665-672`, the lanes' `_consult_resolver` |
| C6.3 | G62: a remote's 202 is an "accepted, waiting" state on the delegate tool, the mission card and the room; the end state arrives through the inbound route | M | `agents/remote.py:546-580,739-768`, `agents/delegate_tool.py:1022-1050`, mission card |
| C6.4 | @-agent round: show the daemon's `entry["error"]` in plain words, a Stop (an abortable panel route), never the raw `panelKind` | S | `app/chat/page.tsx:3134-3143,9906`, `routes/agents.py:2260`, `agents/threads.py:1475` |
| C6.5 | Helper failures through `failure_words.run_failure_words` (mission log, Build helper rows); chat's Iron-Proxy account sentence surfaced as the headline; phone crash lines | S | `agents/agent_tools.py:743`, `agents/delegate_tool.py:819`, `HarnessHelpers.tsx:119-139`, `providers/failure_words.py:753-847`, `comm/inbound.py:1444` |
| C6.6 | Key door and Connections: probe text through `friendly_error`; retire "Subscription & local providers", "Rescan local CLIs", "Sessions use this provider by default", "Server type", `_oauth_client_id`; Channels/SMTP/IMAP words | S | `connections/probe.py:84,98`, `routes/connections.py:1149-1163`, `ConnectDoors.tsx:789-791`, `ConnectionsPage.tsx`, `EndpointModelPicker.tsx:65`, `NotificationsPage.tsx:145,180` |
| C6.7 | Local only words say "this PC or your own network"; "Don't ask" names connected apps, commits and pushes; mission screen says local or cloud; helper rows name the machine | S | `lib/localOnly.ts:36-50`, `lib/permissionLevels.ts:98`, `MissionOutput.tsx:395-415`, `lib/buildHelpers.ts:366` |
| C6.8 | Grok Sign in press | S | `CliSignInPress.tsx`, `lib/cliSignIn.ts`, `providers/cli_detect.py:259` |
| C6.9 | The orb says "Waiting for you" when every running reply is parked on a card | S | `components/MoodOrb.tsx:76-82`, `/health.replies` gains `waiting` |
| C6.10 | Tray notices for a finished mission, a chat turn > 60 s and a chat-started helper (reuse `build.turn_finished`'s shape) | S | `desktop/main.js:3381-3407`, `comm/notifier.bell_pings`, one-notice fixture |
| C6.11 | Session board: stopped runs in a grey "Stopped" lane; "this action has no safe inverse" reworded | S | `lib/kanban.ts:54`, `TurnReceipt.tsx:1439` |
| C6.12 | Fleet form gets a key field (the Connections endpoint form's); Gemini door says "free"; first run offers Import when a Claude/Codex/Cursor/VS Code config is found | S | `app/fleet/page.tsx:846-870`, `ConnectDoors.tsx:104`, `onboarding/*` |
| C6.13 | Nav renames (Sentinels → Watchers, Reflexes → Rules, Artifacts → What agents made) | S | owner's call; canon rename procedure |

### C7. Measure the feel, not only the tests (P1)

- **A latency trace for one real Chat › Code reply** on the user's PC: ms to
  first token, ms per round split by phase (C3.1), ms from `result` to the
  next tool start, ms from last token to the settled reply on screen. Write
  it into `docs/IJ-HARNESS-PLAN-3.md` and repeat after C2/C3.
- **A render budget test** for both Code views: renders per streamed token
  of the transcript rows (≤ 1), per 4 s poll with no change (0).
- **The press count** (C5.4) in both views and both default modes.
- **The bench's Jarvis runner runs a REAL Build turn** (`start_build_turn`,
  with `reply_record`, `recorded_turn`, the finish check and checkers on),
  so the published "as fast as Claude Code" number measures the product
  (`harness_bench/runners.py:8-20,993-998` runs the bare lane under yolo).

---

## Part D. Decisions for the user before these tasks start

- **D1 (C1.3, C3.3):** Keep the finish check (decision 6) and project
  checkers (decision 7), but let them run without a card under a session
  grant and under Don't ask, skip the re-run when the last test already
  passed after the last edit, and run checkers once per reply over changed
  files only. The alternative is the current behaviour with its cards and
  its minutes.
- **D2 (C1.6):** An approved plan's act turn and a person's Resume keep the
  posture the person chose (Accept edits / Auto); night, CI and restart
  resumes keep falling back to Ask first.
- **D3 (C1.8, C5.3):** One posture vocabulary across both Code views, named
  by effect ("Ask before commands" for today's default).
- **D4 (C3.1):** Whether to go beyond keep-alive and background teardown to a
  long-lived `claude` process per Chat › Code session (the hosted driver
  already runs one). It changes the v1.300.0 design; measure first.
- **D5 (C3.2):** A deterministic "what I did last turn" block in the history
  (no native carrier, so account mobility is kept) versus today's text-only
  history.
- **D6 (C5.1):** The names of the two Code views, or whether to fold them:
  one "Code" door whose composer's Tool chip offers Jarvis beside Claude Code,
  Codex, Pi and OpenCode (Build › Code's start composer already has the chip;
  Chat › Code would be the "Jarvis" tool). Folding removes B6 entirely but is
  the largest item here (L) and changes the chat page's switch.
- **D7:** Build › Code without Local only and trust posture stays as designed,
  with the Memory box's warning, or gains a per-session "keep on this
  network" note that greys cloud tools (not enforcement; the real CLI cannot
  be confined).

## Part E. Suggested order

1. **Wave 1 (P0, mostly S):** C1.1, C1.2, C1.3, C1.4, C1.7, C2.1, C2.2, C2.4,
   C4.1, C4.9, C4.7, C1.11. This alone removes most of the "it keeps asking"
   and the blink, and gives Build › Code a plan, a reason and a Retry.
2. **Wave 2 (P1 feel):** C2.3, C2.5, C2.6, C2.7, C2.8, C4.2, C4.3, C4.4,
   C4.5, C4.6, C4.8, C3.4, C3.6, C3.7, C3.8, C7 trace.
3. **Wave 3 (P1 speed, decisions D1/D4/D5):** C3.1, C3.2, C3.3, C3.5, C1.5,
   C1.6, C1.9, C1.10.
4. **Wave 4 (names and the rest of the audit):** C5.*, C6.*, C4.10-C4.12,
   C2.9-C2.11, then the re-driven walkthrough and a re-audit.

Lanes inside a wave own disjoint files: `routes/chat.py` + `chat_turn.py`
(one owner), `HarnessSession.tsx` (one owner), `hosted/claude.py` (one
owner), `BuildCodeView.tsx` (one owner).

## Part F. Do not build

- No second conversation renderer: Chat › Code adopts Build › Code's
  `CodeConversation` items (C2.3), not a new component.
- No new approval component or vocabulary: `lib/approvalWords`, `DockAsk`,
  `ApprovalCard` and `CodeAskCard` converge (C5.3).
- No new "running" or "receipt" backend: `/chat/turns/running`,
  `build.turn_finished`, the reply line and `events.jsonl` already hold it.
- No marketplace, no glossary layer, no model-written error explanations
  (the typed fields already give plain words).
- No change to the Terminals view or the chat page's own renderer.
