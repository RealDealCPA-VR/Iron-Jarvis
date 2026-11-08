# Product-readiness re-audit at v1.452.0: the seven lane reports

The per-item marks behind section 0j of `PRODUCT-READINESS-RESULTS.md`, kept so the next re-audit can mark moves item by item. Each lane read master at v1.452.0 (commit 80ec5e89) read-only on 2026-11-08, starting from its own report in `PRODUCT-READINESS-LANES-v1447.md`; every mark cites the current code. Lane F-K's 68 probe sentences (the 38 of v1.447.0 and 30 new) were run through the real matchers in-process with no model.


---

# Re-audit lane A-B — Actions self-evident + Standardized execution receipt (v1.452.0)

Audited against master at v1.452.0 (commit 80ec5e89), read-only, 2026-11-08.
Every mark was read off the CURRENT code (dashboard components and lib tables,
daemon routes, desktop/main.js); the shipped-notes (0i, 0h.5, CLAUDE.md Wave A-D
and "The owner's four answers") were used only to know where to look. No test
suite was run; nothing went to the running app.

Judged as a first-time user who knows nothing about MCP, harnesses or daemons,
working folder-first (no Projects). Paths are relative to the repo
(`dashboard/` for page code, `src/iron_jarvis/` for the daemon).

## Headline

| Section | Items | PASS | PARTIAL | FAIL | v1.447.0 |
|---|---|---|---|---|---|
| A. Actions self-evident | 33 | **30** | **3** | 0 | 24 / 9 / 0 |
| B. Execution receipt | 24 | **24** | **0** | 0 | 21 / 3 / 0 |

Every move below is a CODE change (Waves A, B and D closed the baseline's
gaps); no mark moved because I read more leniently. One mark (A #2) stays
PARTIAL but for a NEW, smaller reason found by reading the orb's sources
strictly. No regressions.

---

## 1. Per-item table — Section A

### A.1 The nineteen questions

| # | Item | Mark | Move | Evidence (file:line) | Current experience | Missing / smallest fix / size / type |
|---|---|---|---|---|---|---|
| 1 | Did Jarvis actually do what I asked? | PASS | = | `components/chat/TurnReceipt.tsx` (collapsed line); `daemon/change_claims.py`; `lib/statusWords.ts` OUTCOME; `components/buildCode/CodeConversation.tsx:686-712` (Build › Code receipt leads with ✓/⚠, v1.449.0) | Every reply/run leads with its outcome; a claimed-but-not-made change gets a note. | — |
| 2 | Is Jarvis currently working? | PARTIAL | = (new reason) | `lib/runningNow.ts:66-68, 357-510` now lists live workflow runs, media jobs and running night tasks (Wave B item 12, fixed). BUT the orb counts only session events + `/health.replies` (`components/MoodOrb.tsx:80-100, 168-249`) and the home line only `orchestrator._running` + build replies (`daemon/routes/ui.py:74-123`). | Open "Running now" and a media render or a tool-only workflow is listed — while the orb beside it still says "Idle — nothing running" and the home line says nothing. Two answers to one question (rule 9). | Add live workflow runs (no-agent ones) and in-flight media jobs to `/health.replies` (or a `background` count) and to `ui.status_line.running`; MoodOrb's `repliesRunning` already reads it. S. UX/consistency. |
| 3 | What is Jarvis doing right now? | PASS | = | `components/chat/WorkLine.tsx` LiveWorkLine; `components/buildCode/CodeConversation.tsx` streamed rows; `components/agents/mission/LiveActivity.tsx` | Live step with shimmer on every surface. | — |
| 4 | What did Jarvis just do? | PASS | = | `components/chat/WorkLine.tsx`; `components/harness/HarnessMessageRow.tsx` `replyLineParts`; `lib/buildApp.ts:1581-1590` `receiptLine` | One folded line under every reply in the same verbs. | — |
| 5 | Did the operation succeed? | PASS | PARTIAL→PASS (code) | `desktop/main.js:3018-3044` `SPOTLIGHT_OUTCOME_WORDS` + `spotlightOutcome` reads `outcome` ("Needs you", "Part failed", "Stopped"); `lib/statusWords.ts` everywhere else | The Spotlight toast now agrees with every page. | — |
| 6 | Did something partially succeed? | PASS | = | `lib/statusWords.ts` PART_FAILED; `NotificationBell.partFailedBody`; workflows `completed_with_failures`; Spotlight "Part failed" | "Part failed" everywhere, the bell names the step. | — |
| 7 | Did it fail? | PASS | = | bell `BELL_ACTIVITY_TYPES`; main.js `RUN_OUTCOME_AUDIT_TYPES`; `CodeConversation.tsx` `code-error` | Bell, tray, phone, inline alert. | — |
| 8 | Why did it fail? | PASS | = | `providers/failure_words.py` `friendly_error`; chat/Harness/Build › Code Details folds; desktop dialogs now plain (`desktop/main.js:2282 RELEASES_PREVIOUS_HOW`, `:6372 recoveredWords`, Wave D item 39); remote Test worded (`agents/remote.py:178-250 remote_test_words`) | One sentence + one fix, technical text folded. Remaining tail: ~80 press handlers still show `e instanceof ApiError ? e.message : String(e)` (a 5xx reads as the server's envelope text) on Fleet/Skills/Artifacts/Updates/Browser/Creative/Workflows-Stop. | Not enough to drop the mark (the work surfaces are worded); see remaining work #3. |
| 9 | Did Jarvis recover? | PASS | = | TurnReceipt refit/cut-off/failover; `harness/patience.py` provider_wait; `resumedFrom`; bell `build.resumed` | Recovery is said on the line. | — |
| 10 | Why did Jarvis stop? | PASS | = | statusWords STOPPED; kanban Stopped lane; `components/EmergencyStopStrip.tsx`; `STEP_STOPPED_WORDS` | Each cause in its own words. | — |
| 11 | Does Jarvis need something from me? | PASS | PARTIAL→PASS (code) | `components/chat/ThreadGroups.tsx:464-476` a visible "Needs you" pill beside the amber dot (`thread-needs-you-<id>`, Wave A item 7); HomeLine, orb `waiting`, Running now waiting-first, bell pending list | A waiting chat says so in a word, not only a colour. | — |
| 12 | Which agent/harness? | PASS | = | `lib/runningNow.ts` `codingSessionWith`; `lib/agentRun.settledRunName`; mission AgentCards | Named on every row. | — |
| 13 | Which model/provider? | PASS | = | TurnReceipt `turn-answered-by`; `lib/buildApp.ts receiptLine` (model + account); RunMeta; AgentCards | Friendly names everywhere. | — |
| 14 | Which machine/node? | PASS | = (Build › Code improved) | `TurnReceipt.replyWhere`; `MissionOutput.tsx:383 mission-receipt-where`; run page; NEW `hosted/panes.py:1485-1530 where_view` → `components/buildCode/CodePanes.tsx:636 code-session-where` (Wave A item 8) | Chat, runs, missions and now Build › Code's Session tab say local/cloud. LM Studio / llama.cpp under Pi/OpenCode read "not known" (nothing drawn). | — |
| 15 | Which tools/connections were used? | PASS | = | `lib/builtinToolWords.ts` (generated), `lib/runWords.ts rowName`, `chat_turn.app_tool_words` | Tools in words; apps by app name. | — |
| 16 | What files were created or modified? | PASS | = | TurnReceipt `turn-doc` + Undo; ReplyChanges; ChangesReview; Build › Code Changes pane | Files under every reply with Undo/Revert. | — |
| 17 | Where did output/files go? | PASS | = (improved) | `AgentRunReceipt.tsx` folder + Open folder; Spotlight toast `folder` + "Open folder" action (`desktop/main.js:3044, 3089`; macOS draws it, Windows' click opens the run page); Chat › Code's separate copy now OFF by default (`lib/buildGit` `BRANCH_SWITCH_LABEL`, `HarnessStartComposer`, item 43) so folder-first work lands in the folder; "Keep these changes" (`HarnessBranch` `branch-keep`) when it is on | The folder is named and openable; a coding session works in the folder the user picked unless they ask for a copy. | Windows has no "Open folder" button on the toast (the click reaches the run page, which has one). S. Low. |
| 18 | How long did the work take? | PASS | = | `lib/durationWords.ts:13 secondsText` now read by Build › Code (`lib/buildApp.ts:1562-1564 receiptDuration`), chat, RunMeta, mission (`AgentCards.tsx:36, 172`; `MissionOutput.tsx:389 mission-receipt-took`) | One settled vocabulary ("2 min 14 s") on every surface. Live clocks still tick as "1:05" (`components/chat/TurnClock.tsx:17 formatElapsed`, `AgentRunRow.tsx:257`). | Optional: TurnClock's live clock → `elapsedWords` ("1 min 5 s"). S. Cosmetic. |
| 19 | Can I inspect exactly what happened? | PASS | = | StepDetailPanel; HarnessTimeline; Build › Code `expandStep`/"Load earlier"; run page tools table; Activity page now worded (`lib/eventWords.ts` 105 rows, `TimeTravelFeed.tsx:28, 603-711 rowHeadline/humanSummary`) | Every surface opens to its steps; the Activity page speaks in words. | — |

### A.2 Desired summary-then-expand UX

| Item | Mark | Move | Evidence | Current experience | Fix |
|---|---|---|---|---|---|
| Simple by default, detail on expand | PASS | = | Chat TurnReceipt; Chat › Code `reply-line`; Build › Code `<details code-receipt>` whose summary is "✓ answered by Opus 5.5 · 2 min 14 s · 7 actions · 3 files changed", usage inside (`CodeConversation.tsx:697-711`); run page; mission Report | The "✓ Fixed … · 2 min 14 s · 7 actions" shape on every surface. | — |

### A.3 The twelve surfaces — one abstraction or twelve?

| Surface | Mark | Move | What it shares | What it still invents | Fix / size / type |
|---|---|---|---|---|---|
| Chat | PASS | = | TurnReceipt, WorkLine, ApprovalCard (compact + redirect, `ApprovalCard.tsx:259-320, 669-720`), DockAsk (`BOX_COMES_BACK_NOTE`, `DockAsk.tsx:408`), failureLine, statusWords | — | — |
| Build | PASS | PARTIAL→PASS (code) | Terminals: PaneChat → TurnReceipt/WorkLine/PaneAsk→ApprovalCard. Chat › Code: TurnReceipt + WorkLine + HarnessFailure. Build › Code: `secondsText`, ✓/⚠ collapsed receipt with folded usage (Wave B item 10), approvalWords, failure_words. | Build › Code keeps its own `CodeAskCard` (it answers external CLIs' cards; it has the redirect) and its receipt names no place on the line (the Session tab does). Same words, same shape now. | — |
| Agents | PASS | = | RunResultCard + AgentRunReceipt; run page ApprovalCard (`app/sessions/[id]/page.tsx` imports it) | Agent-run asks carry no `why`, so they draw the long card with no redirect (see Approval requests). | — |
| Missions | PASS | PARTIAL→PASS (code) | `MissionOutput.tsx:54, 174` mounts the chat's `ApprovalCard`; tones from `statusWords(...).tone` via `toneInkClass` (`AgentCards.tsx:35, 56-61`); receipt gained duration + action count (`MissionOutput.tsx:362-395`) | Progress-bar fill colours are local (`AgentCards.tsx:74-83`), same tone tokens. | — |
| Tools | PASS | = | packStartWords, StatusChip, bell `mcp.pack_failed`; v1.452.0 Tools menu says a ticked tool runs unasked (`chatControlWords.ARMED_TOOLS_RUN_UNASKED`) | — | — |
| Browser / computer use | PASS | PARTIAL→PASS (code) | `core/risk_door.py:77-172` one resolver registry; chat stream lane cards the risk ask (`daemon/routes/chat.py:5115`), runtime pauses it; no "Pending approval id" reaches a user (only comments mention it; `browser/tools.py:2349` strips it from a step) | The Settings › Connections › Browser queue keeps its own card (`components/settings/pages/BrowserPage.tsx:199-290`): "run <raw run id>" and a `text="…" css=…` dump. Now reached only by the sidebar/fallback paths. | Word the run ("A browser task") and the target in plain words, or mount ApprovalCard. S. UX. (Counted under Approval requests.) |
| MCP | PASS | = | rowName, app_tool_words, tool_setup rows | — | — |
| Remote machines / nodes | PASS | = (fixed its open note) | `waiting_remote` "Will report back"; `agents/remote.py:178-250 remote_test_words` → `SetupCard.tsx:2332-2345` (title + fix, technical folded) | — | — |
| Background work | PASS | PARTIAL→PASS (code) | Running now lists workflow runs, media jobs, night tasks (`lib/runningNow.ts:441-510`); loopWords; bell; EmergencyStopStrip | The orb/home line do not count them (logged under A #2, not twice). | — |
| Scheduled work | PASS | = | scheduleRunWords, On/Paused switch, Emergency stop words | — | — |
| Failures / recovery | PASS | = | failure_words, failureLine, HarnessFailure, `code-error` + Details, failureActions | Press-failure tail (A #8 note). | — |
| Approval requests | PARTIAL | = (narrower) | approvalWords everywhere; ApprovalCard now mounted by chat, Chat › Code dock, run page, mission, PaneAsk, ProjectSurfaces, HarnessHelpers/Attempts; chat card: who asks + why + "No, tell it what to do instead" + "The message box comes back when you answer" (Wave A item 6) | (a) The redirect answer exists only on the chat page (`app/chat/page.tsx:13884 onRedirect={redirectAfterDeny}`) and Build › Code (`CodeAskCard`); Chat › Code's dock passes none (`components/harness/HarnessSession.tsx:3534-3546`), nor the mission card or the run page. (b) Agent-run asks carry no `why`/`asker` (`agents/runtime.py`, `core/approvals.py` have no "why"), so the session page and listing rows draw the long card. (c) BrowserPage's own queue card (raw run id). | (a) Pass `onRedirect` from HarnessSession (deny + `steer`, else send, as `redirectAfterDeny`). S. (b) Runtime asks carry `why` (`auto_approve.card_why`) on `approval.requested`. M. (c) above. S. UX. |
| Common abstraction? | PARTIAL | = (narrower) | Shared spine: failure_words↔failureLine, statusWords (now incl. mission tones, Running now, HarnessHelpers, BuildCodeView), approvalWords + one ApprovalCard on 9 surfaces, generated tool words, eventWords↔notifier (105 types), bell_pings↔bell↔main.js, durationWords (all settled clocks) | Left: the orb/home line count a narrower set than Running now; the redirect answer on 2 of 5 answer surfaces; Build › Code's and BrowserPage's own ask cards. | The A #2 fix + the two approval fixes make it one. |

---

## 2. Per-item table — Section B

| Item | Mark | Move | Evidence | Note / fix |
|---|---|---|---|---|
| Universal receipt | PASS | PARTIAL→PASS (code) | Three data families still (chat `route`, `agents/outcome.session_result(receipt=True)`, Build › Code folded receipt `lib/buildApp.ts:1532-1590`) but ONE presentation: same clock words, ✓/⚠ lead, actions, failed, files; place word on chat/runs/missions and the Build › Code Session tab (`hosted/panes.where_view`) | The data agrees and is drawn the same way. Optional: put `where.words` on the Build › Code receipt line too. S. |
| Objective / request | PASS | = | Session.task; chat question; `mission_view.objective`; hosted `user` | — |
| Current state | PASS | = | statusWords; `hosted/news.running_rows`; `durable_turns.running_replies`; Running now kinds | — |
| Start time | PASS | = | created_at, started_at, timing.startedAt, hosted user.at | — |
| Completion time | PASS | = | finished_at, timing.endedAt, hosted done.at | — |
| Duration | PASS | PARTIAL→PASS (code) | `lib/durationWords.secondsText` read by Build › Code `receiptDuration` (`lib/buildApp.ts:29, 1562`), chat, RunMeta, mission | One settled vocabulary. Live stopwatch "1:05" (TurnClock) and Fleet's "1m 14s" pin expiry (`lib/fleet.ts:536`, not a work duration) remain — cosmetic. |
| Agent | PASS | = | session_result.agent_name; settledRunName | — |
| Harness | PASS | = | `codingSessionWith(tool)`; hosted `harness` | — |
| Model | PASS | = | route.model; session_result.model; hosted session event | — |
| Provider | PASS | = | route.provider; providerDisplay | — |
| Machine / node | PASS | = | route.on_this_network + label; session_result.on_this_network; mission where; Build › Code `where_view` (new) | — |
| Tools used | PASS | = | tools_used; ledger rows with app `name`; hosted tool events | — |
| Important actions | PASS | = | session_result.activity; steps+detail; hosted steps; Timeline calls; mission action count (new) | — |
| Files affected | PASS | = | documents, created_paths, Changes panes | — |
| Result | PASS | = | summary, done.reply, hosted done.result | — |
| Failure | PASS | = | failure_detail; errorKind/Title/Detail; hosted error {title, fix, technical} | — |
| Recovery attempt | PASS | = | refit, cut_off, from/why, provider_wait, resumedFrom | — |
| Approval / intervention | PASS | = | done.approved, auto_approved, approval.* events, InterruptNote | — |
| Child work | PASS | = | helpers on the line, mission members, Build › Code helper children, remote reports | — |
| Final outcome | PASS | = | Session.outcome → statusWords; workflows completed_with_failures; schedules fire_status; Spotlight now reads it | — |
| LEVEL 1 | PASS | = | collapsed lines on every surface | — |
| LEVEL 2 | PASS | = | expanded receipts, reply-line panel, Timeline, run page TeamTree, mission log | — |
| LEVEL 3 | PASS | PARTIAL→PASS (code) | step detail panels, Build › Code "Show all N characters", run page tool outputs, Activity Export; Activity headlines now in words for all 105 published event types (`lib/eventWords.ts`, pinned equal to `comm/notifier.EVENT_WORDS` and `known_event_types()`), payload detail masked | — |
| Does the data exist? | PASS | = | Yes for every field. | — |

---

## 3. Category scores

**Action clarity: 95 / 100** (v1.447.0: 90). Every baseline hold is closed in
code: the chat list says "Needs you" in a word, the Spotlight toast reads the
outcome, the chat approval card says who asks and why and offers "No, tell it
what to do instead" with "the message box comes back", "Setting changed" cards
show option names and link to the row, desktop dialogs and the remote Test are
plain words, the mission screen mounts the chat's card and tones, and the
website risk door is a card that resumes instead of a dead "Pending approval
id". Held back by: the redirect answer missing in Chat › Code (the coding view,
where "do this instead" matters most), agent-run asks with no "why", the
Browser queue card's raw run id, and a tail of press handlers that show a 5xx's
raw text.

**Execution visibility: 94 / 100** (v1.447.0: 89). Running now lists workflow
runs, media jobs and night tasks with Stop on every live Run history row; the
Activity page is worded for every event type; Build › Code's receipt leads with
✓/⚠ in the app's clock words and its Session tab says local/cloud. Held back
by: the orb and the home line still read "Idle — nothing running" during a
tool-only workflow or a media render that Running now lists one click away; a
live stopwatch "1:05" beside settled "1 min 5 s"; and none of it driven in the
packaged app.

---

## 4. Remaining work (ordered by user impact × frequency × confusion)

| # | What | Smallest fix | Size | Type | Where |
|---|---|---|---|---|---|
| 1 | The orb and home line say "Idle — nothing running" while a tool-only workflow or a media job runs (Running now lists it) | Count live workflow runs (no agent step) and in-flight media jobs in `/health.replies` (or a `background` field) and `ui.status_line.running` | S | UX / consistency | `daemon/routes/ui.py:74-123`, `routes/system` /health, `components/MoodOrb.tsx:80-100, 240-249` |
| 2 | Chat › Code's approval dock has no "No, tell it what to do instead" (chat and Build › Code do) | Pass `onRedirect` from `HarnessSession` (deny, then steer the running turn, else send — the chat's `redirectAfterDeny` shape) | S | UX | `components/harness/HarnessSession.tsx:3534-3546` |
| 3 | ~80 press handlers show `ApiError.message` raw (a 5xx's envelope text) on Fleet, Skills, Artifacts, Updates, Browser, Creative and Workflows' Stop | Route press failures through `lib/loadFailed.failureOf`/`serverSentence` (the one load-failed words) | S-M | UX | e.g. `app/workflows/page.tsx:681`, `components/settings/pages/*` |
| 4 | Agent-run asks (run page, listing rows, mission) carry no `why`/`asker`, so they draw the long card with no redirect | Runtime `approval.requested` carries `why` from `auto_approve.card_why` + `asker`; the session page passes `onRedirect` (continue with the words) | M | UX | `agents/runtime.py` `_pause_for_approval`, `app/sessions/[id]/page.tsx` |
| 5 | Browser queue card shows "run <raw run id>" and a `text="…" css=…` dump | Word the run and target, or mount `components/chat/ApprovalCard` | S | UX | `components/settings/pages/BrowserPage.tsx:199-290` |
| 6 | Live stopwatch "1:05" / "12s" vs settled "1 min 5 s" | TurnClock/AgentRunRow use `elapsedWords` | S | Cosmetic | `components/chat/TurnClock.tsx:17, 66, 91`, `AgentRunRow.tsx:257` |
| 7 | Build › Code reply line names no place (only the Session tab) | Append `where.words` to `receiptLine` when known | S | UX | `lib/buildApp.ts:1581-1590` |
| 8 | Spotlight "Open folder" button is macOS-only | Windows toastXml action, or keep the click → run page (has Open folder) | S | Low | `desktop/main.js:3056-3089` |

Ease-of-use items in this lane, verified in code at v1.452.0:
**done** — 11, 19 (`chat-reply-live` role=status, `app/chat/page.tsx:13318`),
20, 21, 22, 23, 24, 30 (`app/workflows/page.tsx:662-830 run-stop-<id>`), 34
(chat; Chat › Code dock still without — remaining #2), 36
(`lib/configCards.ts:25-26, 142`, `ConfigCards.tsx:68-69`), 39
(`ThreadGroups.tsx:464-476`), 41 (`lib/chatList.ts:78-142`, `app/chat/page.tsx:12160`),
46 (`chat-notice` + `chat-notice-live`), 59 (`HomeReadiness.tsx:47-53` required
only), 66 (`desktop/main.js:3018-3089`; Open folder macOS-only), 68
(`main.js:2282, 6372`), 70 (`lib/eventWords.ts`, 105 types), 71
(`history/claude_code.py:89, 166-172` id from the file stem), 72
(`NewSessionForm.tsx:78-87` agentsSettled placeholder), 74
(`lib/buildStuck.ts:18-34`). **Partly** — 34 (Chat › Code). **Open** — none
else in this lane.

---

## 5. Regressions found

None. Every A/B item v1.447.0 marked PASS still holds; spot-checked:
- one-notice rule (bell ↔ main.js ↔ `notifier.bell_pings`) and `RUN_OUTCOME_AUDIT_TYPES` unchanged;
- TurnReceipt collapsed line, WorkLine verbs, StepDetailPanel, the run page's tool table;
- statusWords is still the one table (B17 moved Running now, HarnessHelpers, BuildCodeView, mission onto it);
- v1.452.0's item 43 (separate copy OFF) and item 9 (ticked tools run unasked, menu says so) change no receipt and remove no visibility;
- Wave D's icon sweep (`IconButton`) keeps aria-labels = old words, so no inspect/open control lost its name.

One thing to watch, not a regression: Build › Code's receipt glyph is `aria-hidden`; a screen reader hears the line and the stop words (`code-done`) but not "✓". Acceptable.

---

## 6. Never proven live (this lane)

- None of Waves A-D or v1.452.0 driven in the PACKAGED app: Running now with media/workflow/night rows, the Spotlight outcome toast, the Emergency stop strip, Build › Code's ✓/⚠ receipt, the Session tab's where line.
- The website risk door as a chat card on a real site (G54's fix pinned with fakes).
- The chat approval redirect ("No, tell it what to do instead") with a real model.
- `chat-reply-live` / `chat-notice-live` with a real screen reader (jsdom only).
- A real remote agent's Test failure words; a remote that answers 202 and reports back.
- The Activity page's worded headlines over a real day's events.

---

# Re-audit lane C-D-O — Connections, Import, Marketplace readiness (v1.452.0, commit 80ec5e89, 2026-11-08)

Read-only. Every mark was taken from the code at HEAD (file:line), re-marked from scratch against the baseline lane's bar (`docs/PRODUCT-READINESS-LANES-v1447.md`, lane C-D-O). Nothing on port 8787 or in %APPDATA%\Iron Jarvis was touched; no test suite was run. Judged as a first-time user who knows nothing about MCP, OAuth or model servers, working folder-first (no Projects).

"Move" says why a mark changed: **code** = the code changed since v1.447.0; **stricter** = same code, stricter reading; **=** = unchanged.

## Headline

| Section | Items | PASS | PARTIAL | FAIL | v1.447.0 (PASS / PARTIAL / FAIL) |
|---|---|---|---|---|---|
| C. Connections / capability layer | 21 | 11 | 10 | 0 | 7 / 14 / 0 |
| D. Import what the user already has | 16 | 16 | 0 | 0 | 15 / 1 / 0 |
| O. Marketplace readiness | 11 | 9 | 2 | 0 | 8 / 3 / 0 |
| **Total** | **48** | **36** | **12** | **0** | 30 / 18 / 0 |

## 1. Per-item tables

### C. Connections / capability layer — 11 PASS · 10 PARTIAL · 0 FAIL

| # | Item | Mark | Move | Evidence (HEAD) | What the user meets today | Missing · smallest fix · size |
|---|---|---|---|---|---|---|
| C1 | One understandable place | PARTIAL | = | `connectors/catalog.py:727-735` CATEGORY_ORDER still Developer / Communication / Productivity / Storage / Data & Web / Payments / AI & Media; `DirectoryPage.tsx:1356-1373` MORE_WAYS (→ /agents, /fleet, /tools); `ConnectionsPage.tsx:2142-2163` CONNECT_ELSEWHERE (→ /tools, /channels, /memory) | Apps in the Directory, plans/keys/model servers on Connections, the Model servers page (/fleet, renamed in v1.452.0), Notifications for Slack-as-destination, Memory for drives-as-memory, Tools for extensions. Cross-linked both ways; still five homes and the catalog's own categories. | One Connections landing with Discover / Connected / Import tabs; categories remapped to Development / Productivity / Communication / Accounting / AI & Coding / Local Computer / Advanced. M |
| C2 | GitHub | PASS | = | `catalog.py:254-280` GitHub's hosted server (`transport="http"`, PAT field, token URL in help); chat `app_connect` secure card | Connect, paste a token, done; nothing installed on the PC. | — |
| C3 | Gmail | PARTIAL | = (owner) | `catalog.py:378-391` `needs="Needs a one-time Google developer setup"`, `docs_url` = GongRzhe GitHub README; `DirectoryPage.tsx:909` link now labelled "The program's own page on GitHub" (`lib/directoryLinks.ts:docsLinkLabel`); `page_doors.py:677-684` `gmail_setup` | The card says a Google developer setup is needed; its only guidance is the program's GitHub page. Chat's page step still says "the card's **Docs** link has the steps" — the card no longer calls it Docs. | Owner's decision (item 45 skipped): Gmail keeps the user's own Google app. Smallest honest help: a Help/Handbook section "Gmail: the one-time Google setup" linked from the card the way `ADMIN_GUIDES` links Microsoft 365; reword the chat step's "Docs link". S |
| C4 | Google Drive (and OneDrive, Dropbox) | PARTIAL | code (better) | `components/connections/OAuthAppForm.tsx:39-201` App ID / App secret boxes, redirect address with Copy, saved under `lib/oauthApp.ts:23-30` names; `DirectoryPage.tsx:710-724` "Set up the sign-in (about 5 minutes)"; `ConnectionsPage.tsx:1208, 1296` same form; `lib/oauthApp.ts:39-68` vendor sentence | No more hand-typed internal names (item 16 done). Still: the user must register their own OAuth app in Google Cloud / Entra / Dropbox console. The one sentence ("make one in Google Cloud Console, as a 'Desktop app' OAuth client, allow the redirect address below") omits the consent screen, test users and turning on the Drive API, and a Google *Desktop* client has no redirect-address box to allow — a CPA will likely stall there. "About 5 minutes" is optimistic. | A Handbook section per vendor with the real steps (consent screen, test users, API switch), linked from the card; verified apps would remove the step (owner's call, L). S (words) |
| C5 | Microsoft 365 / Outlook | PASS | code (PARTIAL→PASS) | `catalog.py:393-464` locked card, account-kind choice; `DirectoryPage.tsx:889-897` "How your admin sets this up" → `/help?guide=handbook&section=microsoft-365-locked-down-for-client-data` (`lib/directoryLinks.ts:22, 74-80`; `docs/HANDBOOK.md:6387` the section exists); `DirectoryPage.tsx:909` the GitHub link labelled as the program's page; `DirectoryPage.tsx:1178-1190` "Get Node.js (free)" read off a NO_NODE error (`runtimeLinkInError`) | Read-and-draft only, one account, the firm's app, Sign in to finish; the admin's steps are one press from the card (item 17 done). A personal account needs nothing more. | The work-account app ID is still only a field's help text, not a `needs` line (it says "the Handbook has the steps" right beside the link that opens them — fine). Never proven live. — |
| C6 | Slack | PARTIAL | = (owner) | `catalog.py:353-376` bot token + team id "from your Slack app"; `daemon/app.py:3552-3557, 3577-3581` destination switches now "Listen for incoming messages" / "Chat with Iron Jarvis" as real toggles (`type: bool`, item 53 done) | Two Slacks (an app to read channels, a destination Jarvis messages you on), each pointing at the other. Both need a Slack app the user makes on api.slack.com (the Notifications form gives a manifest). | Owner's decision (item 45 skipped). — |
| C7 | Notion | PARTIAL | = | `catalog.py:465-481` one internal-integration token; `DirectoryPage.tsx:833-839` "Turn it on in a chat with Tools › Connections" | Best app path: one token, Connect; chat "connect my Notion" opens the secure card. A connected app stays off in a chat until toggled or named in the message. | Arm connected apps by default (toggle to turn off), or say on the card "naming it in a message is enough". S |
| C8 | Browser | PARTIAL | code (better) | `extensions/chrome/src/setup/setup.html:21-24, 164-184` consent text rewritten, browser-neutral `[data-browser]` spans; `components/browser/browserWords.ts:113-127` `staleAddonLine` ("…is running an older copy of the add-on (x; this app is y) — open … and press Reload"); `YourBrowserCard.tsx:561`; `BrowserSetupModal.tsx:884-893` Developer mode + Load unpacked; `app/help/page.tsx:157, 236` still "Pair your own Chrome" / "Your own Chrome" | The consent page now says what access really allows, and a stale copy after an update is caught (item 9's first two bullets done). Install still needs Developer mode + Load unpacked (unsigned add-on); Help's tile still names Chrome to an Edge user. | Store listing (owner's call, L); Help tile words browser-neutral (S). |
| C9 | Local files | PASS | = | `DirectoryPage.tsx:1347, 1588` "Built in, nothing to connect: Files & documents, Web, Images, Spreadsheets, Memory" | Nothing to connect. | — |
| C10 | Computer control | PARTIAL | = (decision) | `page_doors.py:777-790` NOT_OFFERED `desktop_control`; `page_doors.py:408-416` `asks_desktop_control` (now also stops a browser click being armed for "click … in the QuickBooks desktop app", Wave A item 3); `lib/nav.ts:300` alias | Chat says plainly it cannot drive desktop programs. No card because the capability does not exist (G85, owner's decision). | By decision; optional one line on the Browser page for a searcher who lands there via "computer control". S |
| C11 | Claude | PASS | code (PARTIAL→PASS) | `ConnectDoors.tsx:753-840` not installed → "Install Claude Code for me" (a visible Build terminal running the vendor's installer) + "I've installed it — check now"; `terminals/ai_clis.py:94-99` `INSTALL_JOBS`; signed-out → `CliSignInPress` + "I've signed in — check now"; `routes/settings.py` `promote_model` | From the chat's first-run doors: install, sign in, use — each one press, in words (item 50 done). | Settings › Connections' Claude row still shows only the vendor's page when not installed (`ConnectionsPage.tsx:2032-2044`) — a user who adds Claude later from Settings meets a developer page. The install lines were never run on a PC without the CLIs. S (reuse `startInstall` on the row) |
| C12 | Codex | PASS | code (PARTIAL→PASS) | Same files as C11 (`codex-install`) | Same as Claude. | Same Connections-row gap. S |
| C13 | OpenAI | PASS | code | `ConnectDoors.tsx:154-170` `keyUrl` platform.openai.com/api-keys + "bills that account per use… separate from a ChatGPT plan" (item 60 done); key tested before stored (`?check=1`, `ConnectionsPage.tsx:1014-1018`, `routes/connections.py:1346, 1441`); the chat's secure card tests too (`settings/credentials.py:260`) | Paste, it is tested everywhere, only a passing key is saved and made default. | — |
| C14 | Gemini | PASS | = | `ConnectDoors.tsx:175-183` AI Studio key, `keyUrl`, free tier words | Key door with where to get one. | — (never tested live) |
| C15 | Ollama | PARTIAL | code (door fixed) / stricter (Settings row) | First-run door: `ConnectDoors.tsx:1015` "Ollama is running but has no model yet — press Download a starter model", `:1084` one "Use Ollama for answers" press, step 2 greyed until installed; `routes/settings.py:1036-1051` refusal names the press, no command; `daemon/app.py:2604-2617` /health carries `installed`/`running`. BUT `ConnectionsPage.tsx:2052-2059, 2642` the Settings row reads only `detected` (= has a model) and so tells a user whose Ollama IS running with no model "Not running on this PC. Get Ollama and download a model" | The chat's door is right (item 49 done). Settings › Connections says something false for the running-no-model case. | Read `running` on the Connections row and show the door's sentence + the starter press. S |
| C16 | LM Studio | PASS | = | `providers/local_servers_seen.py`; `LocalServerRows`, `LocalServerChips`; chat `model_server_add` reads "LM Studio on port 1234" (Wave A item 1) | Running LM Studio shows as a one-press row; chat sets it up. | — (never seen live) |
| C17 | OpenRouter | PASS | = | Connections key path (tested before stored); chat "use OpenRouter" now reaches the provider writers / a page door (`settings/setup_words.py` USE_VENDOR, Wave A item 1) | Same key path as OpenAI. | — |
| C18 | MCP | PASS | code | `DirectoryPage.tsx:695-704` and `ToolsPage.tsx:1307-1316` a real "Get Node.js (free)" link (https only, `lib/directoryLinks.ts:51-63`; item 56 done); no "MCP" in visible strings on Directory / Tools / AddPack | The word never reaches a primary surface; a missing runtime has a real download link. | — |
| C19 | Custom API / tool | PARTIAL | code (better) | Chat doors `settings/records_tools.py:931` `McpServerAddTool` (paste a block / command / address, once-only card, secrets on the secure card) and `:1128` `WebServiceAddTool`; `integrations/builtin.py:116, 183` web services still `Authorization: Bearer` only; `integrations/hookups.py:198-202` refusals "integration name is required" / "base URL is required" (engine words) | Add a tool or a web service from chat or the advanced pages; honest "advanced" labels. | Other auth shapes (API-key header, basic) for web services; plain refusal words. M |
| C20 | Remote Jarvis node | PARTIAL | = | `AnotherJarvisCard.tsx`, `lan_access` setting, `page_doors.py` NOT_OFFERED `another_computer`; no "firewall" anywhere in daemon, dashboard, desktop or Handbook (grep) | One card, one setting; chat honest. A Windows Firewall block is neither detected nor explained; never run on two PCs. | Firewall sentence in the reachability note + Handbook; a two-PC run. M |
| C21 | Local models (model servers) | PASS | code (PARTIAL→PASS) | `daemon/default_replacement.py` called by `routes/fleet.py:467, 516` and `routes/connections.py:1547-1549`; `components/connections/DefaultImpact.tsx` said before the press (item 47 done); `lib/nav.ts:485-494` "Model servers" with the old words as aliases (item 44) | Add from Connections, the Model servers page or chat; removing the default's server names the replacement first. | Still two add forms (`ConnectionsPage.tsx:940`, `fleet/page.tsx:1025`) that both write `/fleet/nodes`. S-M polish |

### D. Import what the user already has — 16 PASS

| # | Item | Mark | Move | Evidence (HEAD) | Today | Missing · fix · size |
|---|---|---|---|---|---|---|
| D1 | Claude Code | PASS | = | `mcp/import_sources.py:582-604` `~/.claude.json`, settings, plugins, the project `.mcp.json` | Found by name; switched-off plugins arrive unticked. | Folder-first gap: the "(this project)" sources (`.mcp.json`, `.cursor/`, `.vscode/`, `.opencode/`, `.grok/`, `.pi/`) read only the ACTIVE PROJECT's folder (`import_sources.py:124-186` `active_project_root`), so a user who works folder-first never has a repo's own config found. S (also read the last-used Build/chat folder) |
| D2 | Claude Desktop | PASS | = | `import_sources.py` `read_claude_desktop` + tool toggles | Servers turned off there arrive unticked. | — |
| D3 | Codex | PASS | = | `import_sources.py:605` | — | — |
| D4 | Cursor | PASS | = | `:606-607` | — | (project file: folder-first gap as D1) |
| D5 | VS Code | PASS | = | `:610-622` | — | (as D1) |
| D6 | Existing MCP configuration | PASS | code (better) | paste preview (`parse_any`), plus chat's `mcp_server_add` (`records_tools.py:931`) takes a pasted block / command line / address | Paste anywhere — the Tools page or the chat — and get a preview of names. | — |
| D7 | Local model servers | PASS | = | `providers/ollama_seen.py`, `providers/local_servers_seen.py` | Detected without a press. | (C15's Settings row words) |
| D8 | Other supported AI tooling | PASS | code (PARTIAL→PASS) | `import_sources.py:629-638` Gemini CLI, OpenCode (+ project), Grok (+ project), Antigravity, Pi (+ project); placeholders `{env:X}` / `{file:PATH}` become fills (Wave C item 30) | Users of the Build › Code tools see their servers found too. | — |
| D9 | Discovery automatic | PASS | = | `DirectoryPage.tsx:1276-1300` "Looking in your other apps" at every Directory open; `OnboardingWelcome.tsx:523` `ImportToolsOffer` on the open `connect_apps` step; chat `app_import` (`records_apps.py:910`, reads `scan()` so the new sources too) | Read-only scan; nothing added without a press. | The offer lives on Everything › Status (`FirstRunStrip` / `StatusOverview`) and the Directory, not on the chat home where a newcomer is. S (one "Found N tools in Claude Desktop — import" line on the chat home) |
| D10 | Import understandable | PASS | = | `AddPack.tsx` `SourcesDoor`, `lib/addPackWords.ts` rowAbout | Rows read in names and programs. | — |
| D11 | Secrets safe | PASS | code (hardened) | `import_config.py` `mark_placeholders`; `PackSpec.preview_row` through `split_args` (Wave C review: an imported `--api-key VALUE` no longer reaches the preview); `mcp/secrets.split_url` masks a credential-named query parameter; chat `mcp_server_add` refuses a secret with a value before any card | Placeholders are asked; values go disk → vault. | — |
| D12 | Explains what was found | PASS | = | `addPackWords.ts` rowAbout; preview rows carry program/host/blurb/exists_as | — | — |
| D13 | Explains what was imported | PASS | = | apply answers `{added, skipped}` with words | "Added, 14 tools" / "Added, but it didn't start: <why>". | — |
| D14 | Shows whether it works | PASS | = | `connectors/service.py` `_mcp_status`, load records | Green only when tools loaded. | — |
| D15 | Failed import repairable | PASS | = | Retry, Change key (EditPack PATCH keeps keys), Test | No restart, no re-entry. | — |
| D16 | Feels like importing | PASS | = | the doors speak in apps and names; chat can do it too | — | — |

### O. Marketplace readiness — 9 PASS · 2 PARTIAL

| # | Item | Mark | Move | Evidence (HEAD) | Gap · fix · size |
|---|---|---|---|---|---|
| O1 | Common capability representation | PARTIAL | = | `connectors/catalog.py` `Connector` serves apps, packs, link cards and the Tools catalog; model providers (ConnectionsPage rows), destinations (`comm`), agents, web services (`hookups`) and model servers (`fleet`) keep their own shapes | Fold providers and destinations into the row shape behind `list_connectors`. M |
| O2 | Mechanisms appear consistently | PASS | = | One `ConnectorCard`; mechanism only as a tooltip | — |
| O3 | Capabilities describe themselves | PASS | = | blurb / unlocks / scopes; `user_pack_blurb` | — |
| O4 | Report health | PASS | = | statuses, `key_check`, load records | Key verdicts still process memory (minor). |
| O5 | Expose setup requirements | PASS | code (PARTIAL→PASS) | `needs`, `runtime_ready`/`runtime_url` + a real download link (`directoryLinks.runtimeDownload`); OAuth cards' requirement is boxes on the card (`OAuthAppForm`), not typed secret names; M365 admin steps one press away (`ADMIN_GUIDES`) | The vendor-console steps behind the OAuth boxes are thin (C4). |
| O6 | Expose permissions | PASS | = | `service.py` `pack_powers`; `mcp/tools.py` `pack_grants_auto` | — |
| O7 | Expose actions/tools | PASS | = | `tool_names` on every row | — |
| O8 | Installed / imported | PASS | = (+chat) | `mcp/pack_door.add_pack` the one door — Directory, Tools, paste, import, chat `app_connect` / `app_import` / `mcp_server_add` | — |
| O9 | Enabled / disabled | PASS | = | PackSwitch; `app_switch` from chat | — |
| O10 | Repaired / reconnected | PASS | = | Retry, Change key, reconnect, Test | — |
| O11 | Intent → capability | PARTIAL | code (better), = (shape) | Wave A widened the matchers: provider/plan words (`settings/setup_words.py` USE_VENDOR / USE_PLAN / SET_UP_VENDOR), brand + "running / on port N" → `model_server_add`, the generic MCP server and web service doors, the desktop-click misfire; still name- and kind-based, stream lane only (the phone never arms apps by design; Build › Code's real CLIs have their own) | Match connected packs' tool descriptions as a second matcher. M |

### Ease-of-use review items in this lane (`docs/EASE-OF-USE-REVIEW-2026-11-07.md`)

| Item | State | Evidence |
|---|---|---|
| 1 ChatGPT/Claude plan saves a fixed API model | Done | `routes/settings.py` `promote_model` (v1.445.0) |
| 3 Wrong key becomes default before tested | Done | `?check=1` on the door AND Settings › Connections (`ConnectionsPage.tsx:1014-1018`) AND the chat's secure card (`settings/credentials.py:260`) |
| 4 "Not signed in" for 2 minutes after sign-in | Done | "I've signed in — check now", `signin_watch.watch_default_login` |
| 9 Browser add-on: consent text, stale copy, install | Partly | consent rewritten + `staleAddonLine` done; Help tile "Pair your own Chrome" (`app/help/page.tsx:157, 236`) open; Developer mode + Load unpacked open (store: owner) |
| 13 Editing a destination quietly changes it | Done | `routes/comm.py` keeps stored keys / events (v1.446.0) |
| 14 One click removes an extension | Done | ConfirmButton on the Tools catalog card (v1.445.0) |
| 16 Drive / OneDrive / Dropbox typed secret names | Done (names) / open (vendor app) | `OAuthAppForm.tsx`; the vendor registration itself remains |
| 17 M365 card "the Handbook has the steps", no link | Done | `DirectoryPage.tsx:889-897` |
| 47 Removing the default's server | Done | `daemon/default_replacement.py`, `DefaultImpact.tsx` |
| 49 Ollama running with no model | Done on the door / partly | `ConnectDoors.tsx:1015, 1084`, `settings.py:1036-1051`; Settings › Connections row still says "Not running on this PC" (`ConnectionsPage.tsx:2052-2059`) |
| 50 Claude Code/Codex not installed → developer page | Done on the door / partly | `ConnectDoors.tsx:753-840`; Settings › Connections row still a link (`ConnectionsPage.tsx:2032-2044`); never run on a clean PC |
| 51 Destination name in use replaces | Done | `routes/comm.py:194, 471` NAME_IN_USE |
| 52 Destination name with a space | Done | `routes/comm.py:190, 451` NAME_RULE_REFUSAL |
| 53 Slack "(true/false)" | Done | `daemon/app.py:3552-3581` |
| 54 One setting, two controls | Partly (by decision) | `SettingsHome.tsx:220-226` adds `browser_access`, backup copy keys; `custom_base_url` / `ollama_base_url` kept as plain rows on purpose (nothing on Connections lets you TYPE one) |
| 55 Obsidian / Notion rows need a restart | Done | `settings/schema.py:848-855` `restart=True`, help points at "Add a memory base" |
| 56 "Needs Node.js" hover-only | Done | `DirectoryPage.tsx:695-704`, `ToolsPage.tsx:1307-1316` |
| 57 Memory base "Source added" unchecked | Done | `routes/knowledge` `read_back` / `added_words` |
| 58 Directory/Tools scroll sideways on a phone | Done (Directory) | `DirectoryPage.tsx:1642, 1688, 1716` `grid-cols-[minmax(0,1fr)]` |
| 60 No "where do I get a key" for Anthropic/OpenAI | Done | `ConnectDoors.tsx:154-170` |

## 2. Category scores

**Connections: 84 / 100** (v1.447.0: 76). Moved up by code: a plan without its CLI installs it from the first-run door in one press (item 50); the Ollama door handles every state honestly (item 49); a key is tested before it is saved on every door (door, Settings, the chat's secure card) and the key panel says where a key comes from and that it is billed per use (items 25/60); removing the default's server names the replacement first (item 47); the M365 card links its admin steps (item 17); the drive cards hold the App ID / secret boxes with a copyable redirect (item 16); "Get Node.js (free)" is a real link (item 56); the add-on's consent page is true and a stale copy is caught (item 9, part); chat can add any tool server or web service (Wave A item 4). What holds it: Gmail, Slack and the three drives still need an app the user registers with the vendor (owner's decision on 45; the drive steps are one optimistic sentence), the browser add-on still installs by Developer mode, Settings › Connections lags the first-run door (CLI install link only; a false "Not running" for Ollama with no model), five homes with the catalog's own categories, a connected app off in every chat until toggled, and the remote node's firewall is never mentioned.

**Import / discovery: 88 / 100** (v1.447.0: 83). The four Build › Code tools' configs are now import sources (D8 PASS), the preview can no longer show an imported `--api-key` value or a `?key=` address, and chat can take a pasted config block. What holds it: a repo's own `.mcp.json` / `.cursor` / `.vscode` config is read only for an ACTIVE PROJECT, which a folder-first user never has; and the "Found N tools — import" offer is on the Directory and Everything › Status, not the chat home.

**Marketplace / capability readiness: 86 / 100** (v1.447.0: 83). Setup requirements now end in a real action on every card (O5 PASS); installs and repairs go through the one door from every surface including chat. A community directory is still "more `CATALOG` rows plus a `needs`/`docs` link" with no architectural change: `Connector` already carries id, category, blurb/unlocks/scopes, mechanism, fields, `needs`, runtime, provenance, `replaces`, locked/local-only flags, and `connectors/service` projects health, powers, tool names and on/off onto every row, while `mcp/pack_door.add_pack` is the one install path. What holds it: model providers, destinations, agents, web services and model servers are not rows of that shape (O1), and intent matching is by name and kind in the chat stream lane only (O11).

## 3. Remaining work (user impact × frequency × confusion, highest first)

| # | Work | Smallest fix | Size | Type |
|---|---|---|---|---|
| 1 | Drive / OneDrive / Dropbox (and Gmail) need the user's own vendor app; the card's one sentence omits Google's consent screen, test users and the Drive API switch, and asks to "allow the redirect address" on a Desktop client that has no such box (`lib/oauthApp.ts:39-68`) | A Handbook/Help section per vendor with the real steps, linked from each card like `ADMIN_GUIDES`; verified apps remain the owner's call | S (words) / L (own apps) | UX |
| 2 | Settings › Connections lags the first-run door: a not-installed Claude Code / Codex row only links the vendor's page (`ConnectionsPage.tsx:2032-2044`); Ollama running with no model is told "Not running on this PC" (`:2052-2059`, reads only `detected`) | Reuse the door's `startInstall` press on the CLI row; read `running` and show "running but has no model yet" + the starter press | S | UX |
| 3 | A connected app is off in every chat until toggled in Tools › Connections (`DirectoryPage.tsx:833-839`) | Arm connected apps by default (toggle to turn off), or say "naming it in a message is enough" | S | UX |
| 4 | Five connection homes, the catalog's own categories (C1) | One Connections landing with Discover / Connected / Import tabs; remap `CATEGORY_ORDER` | M | UX |
| 5 | Browser add-on: Developer mode + Load unpacked; Help still says "Pair your own Chrome" (`app/help/page.tsx:157, 236`) | Browser-neutral Help words now (S); store listing later (owner, L) | S / L | UX |
| 6 | Folder-first: a repo's own `.mcp.json` / `.cursor/` / `.vscode/` / `.opencode/` / `.grok/` / `.pi/` config is read only for the active project (`import_sources.py:124-186`) | Also read the last-used Build / chat working folder | S | missing capability |
| 7 | The import offer is not on the chat home (only Directory and Everything › Status) | One quiet "Found N tools in Claude Desktop — import" line on the chat home while `connect_apps` is open | S | UX |
| 8 | Chat's Gmail step says "the card's Docs link has the steps"; the card now labels that link "The program's own page on GitHub" (`page_doors.py:678-680` vs `directoryLinks.docsLinkLabel`) | Reword the step | S | words |
| 9 | Remote Jarvis node: no firewall sentence; never on two PCs (C20) | Add it to the reachability note and the Handbook; a two-PC run | M | reliability |
| 10 | Web services are Bearer-only; refusals say "integration name is required" / "base URL is required" (`integrations/hookups.py:198-202`) | Header-key and basic auth; plain refusal words | M / S | capability / words |
| 11 | Intent → capability by name and kind only (O11) | Match connected packs' tool descriptions | M | functional |
| 12 | Two model-server add forms (Connections, Model servers page) | One form, the other page links to it | S-M | UX |

## 4. Regressions found

None in a v1.447.0 PASS item. Every C/D/O PASS still holds: GitHub's hosted card, the built-in-tools line, tested keys, the one Sign in press, LM Studio rows, OpenRouter's key path, no "MCP" on primary surfaces, every import source and the Directory scan, the one add/remove door, health/powers/tool names on the row, Retry / Change key / Test.

Two small inconsistencies introduced by the waves (not regressions of a PASS item):
- The chat's `gmail_setup` step still says "the card's Docs link" after v1.449.0 relabelled that link (remaining work 8).
- The Wave C Ollama fix landed on the chat door but not on the Settings › Connections row, which reads only `detected` and says "Not running on this PC" for a running Ollama with no model (C15). The row said the same at v1.447.0, so it is not a regression, but it now contradicts the door's words beside the same daemon answer.

Marks that moved because I read more strictly (not because code changed): C15 stays PARTIAL on the Settings row's false sentence even though the door's item 49 is done; C11/C12 are PASS on the first-run door while the Settings row's link-only gap is listed as remaining work.

## 5. Never proven live (this lane)

- The "Install Claude Code / Codex for me" lines on a PC without the CLIs (0i: never run); the sign-in watch on a really signed-out CLI; `?check=1` against real Anthropic / OpenAI / Gemini / OpenRouter keys.
- Microsoft 365: the locked install, the Microsoft sign-in, "Sign in to finish" and the Handbook admin steps against a real tenant.
- Google Drive / OneDrive / Dropbox: the App ID / secret boxes against a real vendor console and the redirect address `http://localhost:8787/oauth/<provider>/callback` against a real Google Desktop client; Gmail, Slack, Notion, GitHub's hosted server with real accounts.
- Ollama (all three door states), LM Studio and llama.cpp detection (none installed here).
- The browser add-on's new consent page and the stale-copy line in a real browser after an update.
- Import from a real OpenCode / Grok / Antigravity / Pi config on a fresh PC; the remote Jarvis node on two PCs with the Windows Firewall prompt.
- Adding any pack, web service, model server or memory base FROM CHAT against a live service (the one live run declined those cards).
- Any of it in the packaged app on a clean profile.

---

# Re-audit lane E-J — First-run experience and Discoverability (v1.452.0, commit 80ec5e89, 2026-11-08)

Read-only. Every mark below comes from the code at HEAD (80ec5e89), not from CLAUDE.md or the release notes, which were used only to know where to look. Nothing was run against the running app; no test suite was run. One read-only Python import of `settings/schema.py` was run to count the Settings rows per group. Line numbers are from the files at 80ec5e89.

Inputs: the checklist (`IRON JARVIS PRODUCT-READINESS AUDIT.md` §E lines 271-307, §J lines 439-455); the v1.447.0 baseline (`docs/PRODUCT-READINESS-LANES-v1447.md`, lane E-J, lines 338-501); `docs/PRODUCT-READINESS-RESULTS.md` 0i and 0h.5; `docs/EASE-OF-USE-REVIEW-2026-11-07.md` items 1-4, 6, 9, 11, 15, 43, 49, 50, 59-65, 69.

---

## Headline

| Section | v1.447.0 | v1.452.0 | Move |
|---|---|---|---|
| E. Five-minute first run (11) | 6 PASS / 5 PARTIAL / 0 FAIL | **9 PASS / 2 PARTIAL / 0 FAIL** | E4, E6, E10 → PASS (code changed) |
| J. Discoverability (5) | 2 PASS / 3 PARTIAL / 0 FAIL | **3 PASS / 2 PARTIAL / 0 FAIL** | J5 → PASS (code changed) |
| First-run experience score | 82 | **88** | +6 |
| Discoverability score | 74 | **79** | +5 |

Every move is because the code changed; no mark moved on a looser reading. No regression of a v1.447.0 PASS was found. One NEW defect was found in a shipped fix (the "Next:" line, item 61; see E9).

---

## 1. The first-run walkthrough, three users

### Common to everyone (install → first screen)

| Step | What the user sees | Evidence | Where they can get stuck |
|---|---|---|---|
| Download / install | Unchanged: the release page and README say which file, warn about SmartScreen ("More info → Run anyway") and link why it is unsigned; a standard assisted NSIS installer. `desktop/sign.js` exists (signing work in progress) but the build is still unsigned. | `.github/workflows/release.yml:424-435`; `README.md:111-116`; `desktop/package.json` nsis | Unchanged: the unsigned installer (ease item 8, owner in progress). |
| First launch (splash) | "Getting Iron Jarvis ready — this can take a few seconds." **After 15 s the line changes** to "Still starting — the first start after an update can take a minute." (CSS only; reduced-motion swaps without a fade). | `desktop/loading.html:118-126, 147-149, 441-445` | Item 69 closed. Small word slip: on a FIRST INSTALL (not an update) the later line still says "after an update". |
| Start-up failure | Plain cause + Retry / Open logs / Quit (Wave D: raw error logged first). | `desktop/main.js` `classifyStartupFailure` (unchanged shape) | None. |
| The first screen | Chat: "What can I help with?", the centred composer with the folder chip beside "No project" (folder-first works from the first screen), then "Connect a model for real answers" with three doors (*I already pay for Claude or ChatGPT* / *Free & private on this PC* / *I have an API key*), three suggestion pills + More ideas, then ONE quiet machine line. The amber "Demo replies" strip above. | `app/chat/page.tsx:15112-15142`; `components/onboarding/ConnectDoors.tsx:124-140`; `components/SimulatedBanner.tsx:27-44, 146` | **Item 59 closed:** the machine line counts REQUIRED helpers only and folds the rest ("This PC: N of N helpers ready · 1 optional extra not set up · Details"); a PC without Word no longer reads short (`components/chat/HomeReadiness.tsx:70, 84-99, 120-131`). The packaged build's .xls fix is a reinstall sentence, not `uv sync` (`onboarding/doctor.py:713-722`). |
| If they type first | Unchanged: the demo opens "This is the demo model: …", names the press once; "Connect a model" under the box; greyed model-menu rows say "Not connected yet" + "Connect a model…". | `providers/adapters/mock.py:37`; `components/chat/DemoConnectChoice.tsx`; `app/chat/page.tsx:15093-15099` | None. |

### (a) Claude Code or Codex already signed in — **1 press, ~1 minute**

1. The subscription door shows "Ready — choose what answers you" with **"Use Claude (your Claude Code sign-in) for answers"** and where the words go; the amber strip offers the same press. (Unchanged, `ConnectDoors.tsx:336-350`.)
2. One press → `POST /onboarding/use-model` → the plan's own default ("subscription") is saved → **"Done — Claude (your Claude Code sign-in) answers you now. Type your question in the box."** (`HomeReadiness.tsx:159-166`).
3. First useful work: a pill, a dropped file (a work folder under Documents\Iron Jarvis), or a picked folder (folder chip). 

Variants:
- **Installed but signed out — 2-3 presses:** "Sign in to Claude Code" → a Build terminal with `claude`; "Type `/login` there and finish in your browser"; the row turns green by itself, or **"I've signed in — check now"**; then "Use Claude for answers" (`ConnectDoors.tsx:858-935`; `lib/onboarding.ts:716-734`). Unchanged since v1.445.0.
- **NOT installed — the v1.447.0 gap, now closed in the code — ~5 presses, 3-6 minutes:** the row says "Claude Code (the command-line tool) isn't on this PC yet." with **"Install Claude Code for me"** — "Opens a Build terminal that runs Claude Code's own installer where you can watch it — nothing is installed quietly. Or read the vendor's install page." (`ConnectDoors.tsx:752-850`; `lib/cliInstall.ts:29-60`). The job is the vendor's NATIVE installer (`irm https://claude.ai/install.ps1 | iex`; a cmd form for cmd panes), by catalog id only (`terminals/ai_clis.py:76-123, 207-208`). Then "I've installed it — check now" (or found within a minute; `~/.local/bin` is on the search list, `ai_clis.py:325-358`) → Sign in → `/login` → Use Claude. Count: Install (1) → check now (2, optional) → Sign in (3) → browser sign-in → check now (4, optional) → Use (5).
  - Unproven: the install lines have never run on a PC without the CLIs (0i says so). Codex's native installer target folder is not in the module's notes and not obviously on `_extra_bin_dirs` (only `%LOCALAPPDATA%\Programs` itself, not its subfolders) — a GUI-started daemon whose PATH predates the install may not find `codex` until a restart. Claude Code's first-ever `claude` run may show its own theme / sign-in-method screens before `/login` matters; the hint "Type `/login` there" was not checked against that.

### (b) An API key only — **3 presses, 1-3 minutes**

1. "I have an API key" (1) → the tab (picked from the pasted key's shape) → paste → **Connect** (2-3). The key is tested first and stored only on a pass; a refusal says "Not connected — <title>. <fix> Nothing was saved, and what answers you didn't change." with the technical text under Details (`ConnectDoors.tsx:1280-1306`).
2. **Item 60 closed:** every paid tab now says "No key yet? Get one at the Anthropic Console / OpenAI's platform" AND "Anthropic bills that account per use (pay as you go) — it is separate from a Claude plan." (`ConnectDoors.tsx:156-175, 1312-1335`). Gemini keeps its free-tier note.
3. Settings › Connections and chat's secure card now also check before saving (Wave C item 25; not re-read line by line in this lane).

### (c) Nothing at all (no CLI, no key) — **two honest routes**

- **Gemini's free key — about 3-4 minutes, 4 presses:** the Ollama door now ends with "Prefer no download? A Google Gemini key has a free tier — [switch]" plus the free tier's privacy note (`ConnectDoors.tsx:1129-1147`); the switch opens the key door on the Gemini tab with "Get one at Google AI Studio". This is the five-minute route the v1.447.0 lane asked to be named. For a CPA the privacy note matters and is shown.
- **Ollama — 10-20 minutes, 3 presses + an external install:** 1. Download Ollama (external). 2. **"Download a starter model" is greyed until Ollama is on this PC** with "Install Ollama first (step 1) — this turns on by itself once Ollama is on this PC"; "Ollama is running but has no model yet — press Download a starter model." in words (`ConnectDoors.tsx:996-1048`). 3. **One press, "Use Ollama for answers"** (the daemon saves this PC's address as part of the press), greyed until Ollama answers with a model, with "Turns on once a model is downloaded (step 2)." (`ConnectDoors.tsx:1059-1104`). The daemon's no-model refusal no longer gives a command to type (`daemon/routes/settings.py:1036-1052`). Item 49 closed.

### After the model is chosen — "what would you like to do?"

- On the same screen: the Done line, the three pills, More ideas, and while setting up "Connect the apps you use — GitHub, Notion, Slack…" → the Directory (`HomeReadiness.tsx:196-205`; `chat/page.tsx:15140-15142`).
- **New in v1.449.0: one quiet "Next: …" line** in the checklist's own words ("Next: Work with a document — drop a PDF or spreadsheet into the box and ask for a summary. · All steps") (`HomeReadiness.tsx:133-148, 183-194`; `chat/page.tsx:15139`).
- **Defect found (new, in the shipped fix):** the home reads `GET /onboarding` ONCE per mount of the chat page (`homeReadinessAskedRef`, `chat/page.tsx:12311-12321`); `ChatConversation` is not keyed (`chat/page.tsx:3801, 15446`), so "New chat" does not remount it. In the first visit the only read happened while the demo was the default, so `isMock` stays true and `nextStepText` returns "" (`HomeReadiness.tsx:144-148`): **right after the first real answer, a new chat shows no "Next:" line**, and the machine line keeps showing (settingUp is still true from the stale read). The line appears only after the chat page is mounted again (another page and back, or a restart). The test (`__tests__/chat-home-wave-c-v1449.test.tsx:313-336`) feeds a non-demo answer to the first read, so it cannot see this.

---

## 2. Per-item marks

### E. Five-minute first-run experience (11 items)

| # | Item | Mark | Move | Evidence | Current experience | Missing · smallest fix · size · type |
|---|---|---|---|---|---|---|
| E1 | Installation is understandable | **PASS** | = | `release.yml:424-435`; `README.md:111-116`; `desktop/package.json` nsis | Which file, the SmartScreen step and why, a standard installer. | Signing (ease 8) is the owner's, in progress. |
| E2 | Jarvis launches successfully | **PASS** | = (polish closed) | `desktop/loading.html:118-126, 441-445`; `desktop/main.js` start-up gate + dialog | Splash, 90 s gate, one automatic restart, plain cause with Retry / Open logs / Quit; the splash line now changes after 15 s. | Polish: the later line says "after an update" on a first install too — "the first start can take a minute" · S · UX |
| E3 | Checks its environment automatically | **PASS** | = | `onboarding/readiness.py:32-40`; `chat/page.tsx:12305-12321`; `HomeReadiness.tsx:110-131` | The first empty chat reads the end-user checks; one quiet line; full list on Everything › Status. | — |
| E4 | Problems are explained in normal language | **PASS** | PARTIAL → PASS (code) | `HomeReadiness.tsx:70, 84-131`; `routes/settings.py:1036-1052`; `doctor.py:713-722`; `ConnectDoors.tsx:1286-1306` | Required helpers only on the home, optional ones folded; the packaged .xls fix is a reinstall sentence; Ollama with no model points at the starter-model press; key failures in title + fix with Details. | Residue (not enough to hold the mark): two RECOMMENDED rows' fix text on the Details page still says "Reinstall dependencies (`uv sync …`)" for a broken build — `agent_browser` (`doctor.py:309`, also a dev-toolchain row so hidden on the home) and `pdf scan routing` (`doctor.py:335`). Make them the same frozen-aware reinstall sentence as `_xls_missing_fix` · S · UX |
| E5 | Determines what AI capability is already available | **PASS** | = (stronger) | `ConnectDoors.tsx:451-459` (Ollama installed / running / has-model); `lib/onboarding.ts:589-625`; `providers/local_servers_seen`; `connections/AntigravityRow.tsx` | CLIs installed + signed in, Ollama installed vs running vs with a model, LM Studio / llama.cpp, Grok, Antigravity. | — |
| E6 | Connect/select AI without understanding architecture | **PASS** | PARTIAL → PASS (code) | `ConnectDoors.tsx:752-850` (install), `:996-1104` (Ollama), `:156-175` (key links); `lib/cliInstall.ts:29-60`; `terminals/ai_clis.py:76-123` | Every user shape now has an in-app path: one press for a signed-in CLI, Install-for-me + Sign in for a subscriber without the CLI, a tested key with where-to-get-it and billing, Ollama in three gated steps. | Residue: "Claude Code (the command-line tool)", "A Build terminal opened…", "Type `/login` there" still on the path (`lib/onboarding.ts:716-734`; `ConnectDoors.tsx:875`). Plain alternative: "Claude Code (Anthropic's app for your plan)" and "a window opened" · S · UX. Never run live on a CLI-less PC (see §6). |
| E7 | Optional connections can be added easily | **PARTIAL** | = (improved) | `components/settings/pages/DirectoryPage.tsx`; `components/connections/OAuthAppForm.tsx`; `lib/directoryLinks.runtimeDownload`; `HomeReadiness.tsx:196-205`; `components/onboarding/ImportToolsOffer.tsx` | Directory with a "Needs …" chip and "Get Node.js (free)" link, App ID / App secret boxes with Copy for Drive / OneDrive / Dropbox, Microsoft 365's admin-setup link, import from more tools. | Still needs a vendor developer console for Drive / OneDrive / Dropbox (now with boxes), the user's own Google OAuth client / Slack app for Gmail and Slack (owner decided, item 45 skipped), and Developer mode + Load unpacked for the add-on. Import your tools is still offered only on Everything › Status (`ImportToolsOffer`), the chat's apps line is a Directory link only. Smallest in-app step: add "or import the tools you already use" to the apps line · S · UX |
| E8 | User knows when setup is complete | **PASS** | = | `HomeReadiness.tsx:159-166`; `SimulatedBanner.tsx`; `onboarding/checklist.py:240-328` | "Done — X answers you now. Type your question in the box."; the strip goes; step 1 ticks only when a REAL model answers. | — |
| E9 | User knows what to do next | **PARTIAL** | = (fix shipped, but broken in the first visit) | `HomeReadiness.tsx:133-148, 183-194`; `chat/page.tsx:12311-12321, 15139`; `ChatConversation` unkeyed `chat/page.tsx:3801, 15446` | The "Next: … · All steps" line exists, in the checklist's words — but only after the chat page is mounted again. In the first visit, after the first real answer, a new chat shows the pills and no Next line (stale `is_mock`). | Re-read `GET /onboarding` on every `emptyHero` false→true edge (or when `chosenLabel` is set), not once per mount · S · functional. |
| E10 | Useful work within five minutes | **PASS** | PARTIAL → PASS (code) | walkthrough above; `ConnectDoors.tsx:1129-1147` | (a) 1 press; (b) 3 presses with a key link; (c) Gemini's free key is named on the Ollama door with a one-press switch — inside five minutes for anyone with a Google account; Ollama is the honest 10-20 minute route. | The subscriber-without-CLI route is ~3-6 minutes and unproven live. G86 (bundled model) stays the owner's. |
| E11 | Documentation is NOT required for the normal path | **PASS** | = | the doors; `daemon/page_doors.py`; `app/help/page.tsx` | Every normal-path step is on screen; chat answers how-to questions through the Guide with a door. | — |

**E: 9 PASS / 2 PARTIAL / 0 FAIL** (v1.447.0: 6 / 5 / 0).

### J. Discoverability (5 items)

Capability by capability:

| Capability | Discover it? | Purpose obvious? | Way to start? | Surfaced contextually? | vs v1.447.0 |
|---|---|---|---|---|---|
| Chat with files / a folder | Yes — the home; folder chip on the first screen | Yes | Yes | Work folder on attach; folder chip | = |
| Coding sessions (Chat › Code) | Yes — Chat \| Code switch, Ctrl K "Code", Help glossary | Mostly | Yes — start composer; the separate-copy switch is now OFF by default in plain words (`HarnessStartComposer.tsx:10, 262`) | Coding offer from chat | better (item 43) |
| Build / Build › Code | Yes — sidebar | Partly ("Build terminal" is a developer word) | Yes | "Write it in Chat › Code" line under Build › Code only | = |
| Agents / missions | Everything › Work › Agents; Help card | Yes once found | One objective box | **No** — chat never suggests a mission | = |
| Watchers / Rules / Schedules | Everything › Automations; **plain names now** (`lib/nav.ts:282-285, 318-320`; old names kept as aliases) | Yes | Presets; Rules form opens on "a message arrives → an agent does a task" | Chat arms the writers | better (item 44) |
| What agents made / Model servers | Plain names (`lib/nav.ts:448-451, 486-493`) | Yes | Yes | — | better |
| Memory | Everything › Knowledge; "Teach it your style" | Yes | Yes | remember_preference | = |
| Connections | Settings › Connections; the doors; page doors; chat model/provider words now reach a tool or door (Wave A) | Yes | Yes | Yes | better |
| Browser add-on | Settings › Connections › Browser; Help card | **Partly** — Help still says "Pair your own Chrome so Jarvis can see your open tabs and read the page" (`help/page.tsx:157`), while the add-on also clicks and fills and the setup page is now browser-neutral | Yes (Developer mode) | Page door | = (Help line still stale) |
| The phone | Notifications; Help card | Yes | Yes | Page door / not-offered line | = |
| Night shift | **Only** the Tonight row in Chat › Code; no Everything entry, Help card or Ctrl K row (no "night"/"tonight" alias in `lib/surfaces.ts:115-150`) | Not until opened | Yes (now a folder picker + "Make this folder a repository") | `night_task_add` on "tonight" words | = |
| Try on several models | Chip in the start composers only | Partly | Yes | Two named models in chat | = |
| Local only | Composer chip + ⋯ row | Yes with title | Yes | **Never offered** — a client document attached is never met with "keep this chat on this PC?" | = |

| # | Item | Mark | Move | Evidence | Current experience | Missing · smallest fix · size · type |
|---|---|---|---|---|---|---|
| J1 | Can a new user discover that it exists? | **PARTIAL** | = (improved) | `lib/surfaces.ts:46-150`; `app/help/page.tsx:46, 768-770` ("Where things are"), `:1039` (shortcuts); `lib/helpPlaces.ts`; `app/everything/page.tsx` | Five sidebar rooms; Everything lists every page; Help's new "Where things are" card names the five places; Ctrl K reaches pages, settings, chats. | Night shift, Try on several models and Local only are still not listed anywhere outside their own composers (no Help card, glossary row, Everything entry or Ctrl K alias). Add three glossary rows + a "night shift"/"tonight" alias on the Code palette row · S · UX |
| J2 | Is its purpose obvious? | **PARTIAL** | = (improved) | `lib/nav.ts:282-285, 318-320, 448-451, 486-493` (renamed); `help/page.tsx:86, 98, 56`; `lib/nav.ts:481, 551` | The four engine-room page names are plain now (Watchers, Rules, What agents made, Model servers). | Help's Browser card still understates and names Chrome only (`help/page.tsx:157`); Help says "Connect a model with an API key or OAuth; … encrypted, write-only vault" (`:86`), "Chain several sessions into a … pipeline" (`:56`); Everything blurbs "Your accounts and model endpoints" (`nav.ts:481`) and "Token spend … across your providers" (`nav.ts:551`). Five one-line rewordings · S · UX |
| J3 | Is there an obvious way to start using it? | **PASS** | = | `components/buildCode/StartComposer.tsx`; `HarnessStartComposer.tsx`; `MissionComposer.tsx`; `TonightList.tsx`; Directory Connect | A box or one button for every capability. | — |
| J4 | Does Jarvis surface relevant capability contextually? | **PASS** | = (stronger) | `daemon/page_doors.py` (incl. `model_provider`, `another_computer`); `settings/setup_words.py`; `settings/records_tools.py`; `tools/autoselect.py`; `components/chat/DoorsStrip.tsx` | Page doors, true not-offered lines, the coding offer, connect/failure doors, and since Wave A: "use Claude for this chat", "set up Gemini", "Ollama is running, use it", a pasted MCP block, a custom web service, a second Jarvis all reach a tool or a door. | Still never suggested: a mission for a big objective, Local only for a client file, Night shift, Try on. Not enough to hold the mark. |
| J5 | Is the UI overloaded with features that should appear when relevant? | **PASS** | PARTIAL → PASS (code) | `components/settings/SettingsHome.tsx:237-250, 490-494, 659`; `settings/schema.py` (50 `advanced` rows, 7 dict rows) | Settings shows each group's everyday rows and folds the advanced + JSON rows under a closed "More options (N[, M changed])"; search and `?focus=` open them. Rows shown / folded per group: models 8 / 27, connections 9 / 5, system 6 / 6, appearance 3 / 1, permissions 7 / 0, memory 21 / 5, automation 28 / 7. | Polish: Automation still shows 28 rows and Memory 21 unfolded; the night-shift and curator numbers could join the fold · S · UX |

**J: 3 PASS / 2 PARTIAL / 0 FAIL** (v1.447.0: 2 / 3 / 0).

### Ease-of-use review items in these sections

| Item | State | Evidence |
|---|---|---|
| 1 Use ChatGPT picks a model ChatGPT won't run | Done | `promote_model` (v1.445.0), unchanged |
| 2 Demo is a maze | Done | strip, menu rows, DemoConnectChoice, reply note |
| 3 Wrong key becomes default before tested | Done (now also Settings and the secure card) | `ConnectDoors.tsx:1292`; `settings/credentials` `check_key_first` (Wave C) |
| 4 "Not signed in" for 2 minutes after sign-in | Done | sign-in watch + "I've signed in — check now" |
| 6 File Search looks in the app's folder | Done | `filesearch/defaults.py` (v1.445.0) |
| 9 Browser add-on setup page / stale copy | Partly | setup page browser-neutral, stale-copy line (`browserWords.ts:113`, `YourBrowserCard.tsx:561`); store listing owner's; Help's Browser card still stale (`help/page.tsx:157`) |
| 11 Two Code views look alike | Done | one-line links both ways (v1.445.0) |
| 15 Settings shows ~50 expert rows | Done | `SettingsHome.tsx:237-250, 490-494` |
| 43 Chat › Code branch on by default | Done | `HarnessStartComposer.tsx:262` (off), plain words |
| 49 Ollama door | Done | `ConnectDoors.tsx:996-1104`; `routes/settings.py:1036-1052` |
| 50 CLI not installed → developer page | Done in code, unproven live | `ConnectDoors.tsx:752-850`; `terminals/ai_clis.py:76-123` |
| 59 "5 of 6 helpers ready" | Done | `HomeReadiness.tsx:84-131` |
| 60 No "where do I get a key" | Done | `ConnectDoors.tsx:156-175, 1312-1335` |
| 61 Nothing says what to try next | **Partly** — the line exists but never shows in the first visit (stale read) | `chat/page.tsx:12311-12321`; `HomeReadiness.tsx:144-148` |
| 62 Handbook says untrue things | Partly | overview now "five places" (`HANDBOOK.md:21, 107`); every old release section is still IN the Handbook (e.g. `HANDBOOK.md:4865-4896` "Build is the terminals only", listing "Reflexes, Sentinels"), which the Guide reads and the changelog is generated from |
| 63 Guide reads CLAUDE.md | Done | `guide/corpus.py:44-52` |
| 64 Recommended Settings out of date | Done | `docs/RECOMMENDED-SETTINGS.md:1-15` in Settings' labels |
| 65 What's new shows nothing | Done | `UpdatesPage.tsx:64-88` (`/updates/changelog`) |
| 69 Splash "a few seconds" | Done (one word slip) | `loading.html:118-126, 444-445` |

---

## 3. Category scores

**First-run experience: 88 / 100** (v1.447.0: 82). Wave C closed every first-run item the v1.447.0 lane named and this lane confirmed each in the code: the subscriber without the CLI gets "Install Claude Code / Codex for me" (the vendor's own installer, in view), the Ollama door is gated and ends in one press with a plain no-model line, Gemini's free key is named as the five-minute route, both paid key tabs say where to get a key and that it is billed per use, the home counts only required helpers, the splash changes its words after 15 s, and a "Next:" line exists. A user of any of the three shapes now has an in-app path to a real answer, two of them inside five minutes for sure and the third inside five with a Google account. What holds the score: the new "Next:" line never shows in the very first visit because the home reads its facts once per mount (a real defect in the fix); the install-for-me lines have never run on a PC without the CLIs, and whether a daemon started before the install finds Codex is not shown in the code; "command-line tool", "Build terminal" and "`/login`" are still on the subscriber's path; optional apps still need vendor developer consoles (owner-level); the installer is unsigned; and nothing of the first run has been driven in the packaged app on a clean profile.

**Discoverability: 79 / 100** (v1.447.0: 74). The nav's four engine-room names are plain now (Watchers, Rules, What agents made, Model servers, old names still find them), Settings folds its expert rows, Help names the five places and the real hotkeys, the Guide no longer reads the developer manual, Updates shows what changed, and a first-time user's words about models, MCP servers, web services and a second Jarvis now reach a tool or a door. What holds the score: Night shift, Try on several models and Local only are still invisible outside their own composers and never suggested — a CPA attaching a client's return is never offered Local only, a big objective is never offered a mission; Help's Browser card still says "Pair your own Chrome … see your open tabs", and Help / Everything still say "OAuth", "write-only vault", "sessions", "model endpoints", "Token spend … providers"; the Handbook the Guide answers from still carries every old release section, so the Guide can quote yesterday's layout.

---

## 4. Remaining work (ordered by impact × frequency × confusion)

| # | Item | Smallest fix | Size | Type |
|---|---|---|---|---|
| 1 | **"Next:" never shows in the first visit** — `GET /onboarding` is read once per mount (`chat/page.tsx:12311-12321`), so after the first real answer a new chat still holds the demo-era facts: no Next line, machine line still shown. | Re-read on every `emptyHero` false→true edge (or when `chosenLabel` is set); test the demo → press → answer → New chat sequence. | S | functional |
| 2 | **Local only is never offered for sensitive files** (the owner is a CPA firm); Night shift / Try on / Local only invisible outside their composers. | A one-time "Keep this chat on this PC?" line when a chat attaches a document and a local model is connected; glossary rows for the three; a "night shift / tonight" alias on Ctrl K's Code row. | M (offer) / S (rows) | UX |
| 3 | **Install-for-me unproven; Codex's install folder** — never run on a CLI-less PC; Codex's native install location is not in `_extra_bin_dirs` (`terminals/ai_clis.py:325-358`) as far as the code shows. | Run both installers once on a clean Windows profile; add Codex's real install folder to the search list; check the `/login` hint against Claude Code's first-run screens. | S | reliability |
| 4 | **Help's wording**: Browser card (`help/page.tsx:157`), "OAuth … write-only vault" (`:86`), "Chain several sessions" (`:56`); Everything blurbs "model endpoints" (`nav.ts:481`), "Token spend … providers" (`nav.ts:551`). | Five one-line rewordings in the canon's words. | S | UX |
| 5 | **Chat never suggests a mission** for a long multi-part objective. | A quiet door "Give this to the team as a mission" from the escalation seam. | M | UX |
| 6 | **The Handbook still holds every old release section**, which the Guide reads (`HANDBOOK.md:4865-4896` and many more). | Move release sections out of HANDBOOK.md into the generated CHANGELOG's source (the generator already exists), or weight them below the overview in the Guide's retrieval. | M | UX |
| 7 | **Subscriber path words**: "Claude Code (the command-line tool)", "A Build terminal opened", "Type `/login` there" (`lib/onboarding.ts:716-734`; `ConnectDoors.tsx:875`). | "Claude Code, Anthropic's app for your Claude plan"; "a window opened"; keep `/login` but say "if it asks how to sign in, pick your Claude account". | S | UX |
| 8 | **Import your tools only on Everything › Status.** | Add "or import the tools you already use" to the chat's apps line, opening the same SourcesDoor. | S | UX |
| 9 | **Two `uv sync` fix texts** on recommended rows (`doctor.py:309, 335`). | The frozen-aware reinstall sentence `_xls_missing_fix` already uses. | S | UX |
| 10 | **Splash later line says "after an update"** on a first install (`loading.html:445`). | "The first start can take a minute." | S | UX |
| 11 | Settings' Automation group still shows 28 rows, Memory 21. | Fold night-shift windows, curator numbers, retention under "More options". | S | UX |
| — | Owner-level: unsigned installer (in progress); Gmail / Slack own developer apps (item 45, skipped); add-on store listing; G86 bundled model. | — | — | — |

---

## 5. Regressions

**None found undone** against the v1.447.0 lane: the demo exits, the tested key, the plan's default on promotion, the Sign in press + check now, Build's `?focus=` opening the terminal, the coding offer, the Guide's feature-question rule all still hold.

New defect in a shipped fix (not a regression of a PASS): **the "Next:" line (item 61) cannot show in the first visit** (§1, E9). The v1.447.0 lane's small honesty gap ("Model servers tab cannot say Nothing was saved") is closed (`ConnectDoors.tsx:1290-1293`, Wave C reads the `?check=1` answer).

Drift: `docs/HANDBOOK.md` still contains "Build is the terminals only" and the old page names inside historical release sections (labelled by version, so not false history — but read by the Guide).

---

## 6. Never proven live

- A clean-profile packaged first run: SmartScreen, the 90 s gate, the splash's 15 s swap, the first chat, the doors.
- "Install Claude Code / Codex for me" on a PC without them; whether the running daemon then finds the program; Claude Code's first-run screens vs the "/login" hint.
- The sign-in watch on a really signed-out CLI; `?check=1` against real Anthropic / OpenAI / Gemini keys.
- Ollama installed / running / no-model detection, the gated starter-model press and the one "Use Ollama" press (no Ollama here); LM Studio / llama.cpp chips.
- The Gemini free-tier switch end to end with a real Google account.
- OAuth App ID / secret boxes against a real Google / Microsoft / Dropbox console; the add-on stale-copy line on a real older add-on.
- The "Next:" line after a real first answer (and the first-visit defect above) in a real browser.
- The five-minute timing with a real new user.

---

# Re-audit lane F-K — v1.452.0 (commit 80ec5e89), 2026-11-08

Read-only on the repo. Code read at HEAD; the matchers were run in-process on a SCRATCH app (`create_app` on a temp home; USERPROFILE/HOME/APPDATA/LOCALAPPDATA, OpenCode/Grok/Antigravity/Pi homes and `IRON_PROXY_DATA_DIR` pointed at empty temp folders; `IJ_TEST_NO_NETWORK=1`, code host off; no model, no daemon, nothing sent, never port 8787). The probe calls exactly what both chat lanes call: `chat_turn._resolve_armed_tools` (POST lane + the stream lane's armed set), `tools/autoselect.select_ask_tools` then `chat_turn.settings_ask_tools` (the stream lane's ask tier, which reaches `settings/trigger` and `settings/records.record_tools_for`), the apps hop (`app_names.apps_named` → `resolve_kinds` → `app_states` → `connect_offers`, as `chat_turn.py:8932-8946,9117-9128`), `page_doors.page_steps` / `not_offered` / `asks_desktop_control`, and `autoselect.coding_offer`. Files beside this report: `probe.py`, `fmt.py`, `old38.txt`/`old38.out`, `new30.txt`/`new30.out`, `var.txt`/`var.out` (variants), `reg.txt`/`reg.out` (v1.452.0 rename check).

## Headline

| | v1.452.0 (now) | v1.447.0 |
|---|---|---|
| F marks | **6 PASS / 0 PARTIAL / 0 FAIL** | 4 PASS / 2 PARTIAL |
| K marks | **11 PASS / 0 PARTIAL / 0 FAIL** | 9 PASS / 2 PARTIAL |
| Old 38 probes | **34 right / 4 partly / 0 miss** | 21 / 5 / 12 |
| New 30 probes | **20 right / 7 partly / 3 miss** | — |
| Discoverability | **85** | 76 (results 0h.2; this lane's own number was 78) |
| Trust | **92** | 86 |
| Regressions | none found | none |

Moves: F1, F5, K5, K10 PARTIAL → PASS. F1/F5/K10 are CODE changes (Wave A: provider shapes, `mcp_server_add`, `web_service_add`, the `another_computer` and `model_provider` lines, Build › Code's "Where it works"). K5 is the OWNER'S DECISION (item 9) plus one visible line of words; the code path a writer takes is unchanged, and the hand-pick that made it dangerous no longer has a renderer.

---

## 1. Per-item table

### F. Natural-language capability discovery

| # | Item | Mark | Move | Evidence (file:line) | Experience | Missing · smallest fix · size |
|---|---|---|---|---|---|---|
| F1 | Determine that a requested capability exists | **PASS** | PARTIAL → PASS (code) | Vendors/plans: `settings/setup_words.py:303` `_REQUEST_AT`, `:325` `USE_VENDOR`, `:338` `USE_PLAN`, `:347` `SET_UP_VENDOR`, `:370` `names_a_model_provider`, read by `settings/trigger.py:244` `_EXTRA_REFERENCES`; running local servers: `settings/records_connections.py:1849` `_clause_tools`; MCP server / web service: `settings/records_tools.py:931` `McpServerAddTool`, `:1128` `WebServiceAddTool`; desktop: `daemon/page_doors.py:361` `_DESKTOP_RULES` + `select_ask_tools` dropping `browser_*`; remote node: `page_doors.py:443` `_ELSEWHERE_RULES`; apps `connectors/app_names.py:493,1294,1359`; Guide `tools/autoselect.py:~1100` `_FEATURE_QUESTION`; coding `autoselect.py:3760`. | Every one of v1.447.0's 12 misses now reaches a tool, a door or a true line: "Use Claude for this chat", "ChatGPT Plus plan", "Set up Gemini", "Use OpenRouter" → settings tools + Connections door; "Ollama … use it", "LM Studio on port 1234" → `model_server_add`; "filesystem MCP server" → `mcp_server_add`; "pricing API at https://…" → `web_service_add`; "Jarvis on my office PC" → `another_computer`; Obsidian vault → `memory_base_add`; ClosePilot folder defaults and reviewer → `build_*`; QuickBooks desktop click → `desktop_control`, no browser click; "depreciation schedules" → nothing. | Phrasing gaps (probe §2): "I want to use my Claude Pro / ChatGPT subscription with Jarvis" arms NOTHING (USE_VENDOR's tail takes "for answers/to answer/instead", not "with Jarvis/with you"); "Only let Jarvis look at / read C:\Clients" misses `readable_folders` (only "… my Documents folder" is pinned); feature questions about the other pages ("What are Watchers for?", "What is the Rules page?", "How do I make a rule?") arm no Guide (only Build/Code/missions/apps are in `_FEATURE_QUESTION`). Fix: add `with\s+(?:jarvis|iron\s+jarvis|you|this\s+app)` to `ANSWERS_TAIL` (S); a readable-folders shape "only let (jarvis|you) (look at|see|read|open) <path>" (S); `_FEATURE_QUESTION` rows for each `lib/nav` label + alias (S). |
| F2 | Determine whether it is available | **PASS** | = | `app_names.py:1462` `app_states` (connected/off/not working/signin/not_connected; CLIs from cached verdicts); probe rows show e.g. `codex-cli unknown`, `slack not_connected`; `/health` CLI rows `installed`/`signed_in`; Build › Code tool rows. | The model is told each named app's state; down providers are said before typing. | — |
| F3 | Determine whether it is configured | **PASS** | = | `app_names.py:1721` `apps_section`; `page_doors.py:608` STEPS (gmail_setup, M365 admin, slack_setup, accounts, browser_addon…); `mcp/tools` load records. | "Connect my Gmail…" → `app_connect` + the Gmail door + the `gmail_setup` step (Google developer setup still needed). | The `model_provider` door shows even when that vendor is already connected (text-only rule) — harmless but can read as "not set up". |
| F4 | Tell the user what is missing | **PASS** | = (its v1.447.0 remainder closed) | `page_doors.py:775` NOT_OFFERED (desktop_control, another_computer, jarvis_backup, watcher_suggests, builtin_agent_limit, voice_press, internet_calls_in, email_rule_inbox, full_access_default); `daemon/change_claims.py` via `chat_turn.py:11508` / `routes/chat.py:7673`; `doors.py:223` `failure_door_for`. | "Click the Submit button in the QuickBooks desktop app" and "control my mouse … in Drake Tax" both get the desktop line; "Back up my Jarvis data to my OneDrive folder every night" gets the backup line beside the settings tools. | "control my mouse … Drake Tax" also auto-arms `pdf_form_fill`/`pdf_form_fields` (the word "form"); the true line is there, the noise could send the model to a PDF. |
| F5 | Lead the user directly to the solution | **PASS** | PARTIAL → PASS (code) | `page_doors.py:726` `model_provider` door ("Open Connections to set up a model"), Connect door `doors.with_connect_door` (`routes/chat.py:7732`), page door `with_page_door` (`:7735`), coding offer (`:7729`), secure cards on `mcp_server_add` / `web_service_add` / `model_server_add`, `account_add` → `accounts` door. | Of section C's 20 connection kinds, ~19 now lead somewhere from a fresh sentence (v1.447.0: 13): Gemini, OpenRouter, LM Studio, a custom API, a generic MCP server and computer control joined. | Still short: "Install Codex for me" → Connections door + a `shell` card, while the one-press install job (`lib/cliInstall.ts`) is only on chat's ConnectDoors (empty state / demo strip); "Set up the browser extension in Edge" → no add-on door (the rule wants "add-on" or "you/Jarvis", `page_doors.py:288-313`); "Fix the failing test in my ClosePilot repo" → no coding door (a name between "my" and "repo" breaks `_REPO_TASK_RX`; "my repo" works). Fixes: a `cli_install` page step with the install press (S/M); `<Name> repo` in `_REPO_TASK_RX` (S). |
| F6 | Resume the original task afterward | **PASS** | = | `lib/connectedElsewhere.ts`, `CredentialStore._app_outcome` Continue, `lib/appConnect.ts` `tool_server.*` save → "Tool server “x” added · N tools" and the pack turned on for the chat (Wave A). | After a card or an outside connect, Continue re-sends the request with the tools armed. | Newest reply only (by design). |

### K. Trust

| # | Item | Mark | Move | Evidence | Experience | Missing · fix · size |
|---|---|---|---|---|---|---|
| K1 | What is Jarvis doing? | **PASS** | = (wider) | `lib/runningNow.ts:11-40` (chat turns, helpers, coding sessions, and since v1.449.0 workflow runs, media jobs, night tasks), MoodOrb, `routes/ui.py` status line, `EmergencyStopStrip.tsx`. | One popover for everything that runs, opened only on press. | — |
| K2 | Why is Jarvis doing it? | **PASS** | = | `core/auto_approve.card_why`; compact ApprovalCard with `why` (`components/chat/ApprovalCard.tsx:185,263`); mission screen now mounts the chat's card (Wave B). | Chat cards say why and who asks; steps open to input/output. | Agent-run asks (`agents/runtime.py` — no `why`) and Build › Code's tool cards still show the long card with no reason line (unchanged remainder). S: pass `card_why` from `_pause_for_approval`. |
| K3 | Where is it happening? | **PASS** | = (wider) | TurnReceipt `replyWhere`; mission receipt; `hosted/panes.py:1430,1485` `where_view` → `CodePanes.tsx:600-636` "Where it works". | Chat reply names model + local/cloud; coding sessions name folder, branch, and now where the model answers. | — |
| K4 | What access does Jarvis have? | **PASS** | = | `routes/access_summary.py`, `AccessSummary.tsx`, `lib/permissionLevels.ts:70-118`; the Tools menu's "Turned on for this chat" list with its line (`app/chat/page.tsx:14705-14745`). | Settings block + level meanings on pause + the per-chat list. | — |
| K5 | What will require my approval? | **PASS** | PARTIAL → PASS (owner's decision + words) | `lib/chatControlWords.ts:112` `ARMED_TOOLS_RUN_UNASKED` = "These run in this chat without asking you first (a few safety checks still ask). Press × to turn one off.", shown at `page.tsx:14716` above the armed rows; the per-tool "+" picker (`toolGroups` `:7316`, `toggleTool` `:7682`) has NO caller in JSX, so a writer arms only through a card's "Allow for this chat", a saved chat's setup, or project file tools; once-only/priced/ask-floor/Local-only cards still card (`routes/chat.py:5990-6010`); safety cards stay off the phone from every lane (`core/approvals.py:96` `SAFETY_REASONS`, `comm/prompts.py:572-579`). | A tool the user OK'd for this chat runs on without a card, and the menu that lists it says so. | Words true, and if anything they UNDERSTATE: under "Ask before every action" a file-writing tool in that list still cards (`STRICT_ASK_TOOLS`, `routes/chat.py:5998-6002`). Visible only inside "+" › Tools; the chip's level descriptions do not mention it (acceptable: the user pressed "Allow for this chat"). `SAFETY_REASONS` lacks `memory` (an agent run's `brain_change` ask can still reach the phone) — S. |
| K6 | What happened while I was away? | **PASS** | = | NotificationBell "While you were away", `/sessions/interrupted`, tray notices, night summary, reflex/watcher/workflow failure rings (v1.446.0). | — | — |
| K7 | Can I stop it? | **PASS** | = (wider) | Turn Stop, @-round Stop, Build › Code Stop kills the step, Emergency stop strip, schedule switch; every live Run history row has Stop (v1.449.0). | — | Running now rows open the place, no Stop on the row (acceptable). |
| K8 | Can I undo / recover? | **PASS** | = (its remainder closed) | Setting cards show option names (`settings/tools.py:230` `value_label`, `:532-547`; `chat_turn.py:5590`; `ConfigCards.tsx:68-69`) and link `/settings?focus=<key>` (`lib/configCards.ts:142`); record Undo refuses over newer work; `change_undo`; Rewind; Revert; restore writes a safety backup. | "Setting changed: Ask before commands → Auto" with Undo and a link to the row. | — |
| K9 | Can I inspect its work? | **PASS** | = | Step detail panels, Timeline, ChangesReview, Activity (every event worded since v1.449.0), Build › Code panes, `GET /build/sessions/{id}/harness`. | — | — |
| K10 | Can I tell local work from cloud work? | **PASS** | PARTIAL → PASS (code) | `hosted/panes.py:1485-1525` `where_view` (Claude Code/Codex/Grok/Antigravity = cloud; Pi/OpenCode by provider prefix or Iron Jarvis's own address through `egress.stays_on_this_network`; else not known); `CodePanes.tsx:636` `code-session-where`. Chat side unchanged (`replyWhere`, model menu badges, Local only words). | Build › Code's Session tab says "Where it works". | Only on the Session tab (not on the reply line); a Pi/OpenCode session on a server Iron Jarvis has no address for says nothing ("not known", by design). |
| K11 | Can I tell which machine handled something? | **PASS** | = | `lib/onboarding.machineName`, `lib/answeredModel.ts`, `lib/ranOnWords.ts`, `runPageWords.runModelWords`, `lib/remoteTask` ("From desk"). | "answered by GLM on DGX Spark". | — |

**Things that technically work but would make a new user nervous (now):** (1) "I want to use my Claude Pro subscription with Jarvis" gets no tool, no door — the reply is the model's own guess; (2) a sentence about the user's OWN schedule ("My schedule is packed this week", "Schedule time to review the Smith return") ask-arms the schedule writers (carded, but a card offering to "update a schedule" would surprise); (3) "Whenever a new PDF shows up in my Downloads folder, rename it" arms `reflex_create`, whose sources are webhook/comm/email/calendar/slack (`reflex/models.py:24`) — no file source, and the true `watcher_suggests` line does not fire because the sentence lacks "watch"; (4) daemon sentences on the coding screens still say "Iron-Proxy" (0i open); (5) the `model_provider` door appears even when that vendor is connected. The five of v1.447.0 (silent hand-armed writer, wire values on cards, colour-only dots, hidden box with no "do this instead", agent safety cards to the phone) are all closed.

---

## 2. Probe table

Legend: ✓ right · ◐ partly · ✗ miss/misfire. "armed" = POST + stream armed; "ask" = stream ask tier; doors = Connect / page step / not-offered / coding.

### The baseline's 38, re-run

| # | Sentence | Now | Verdict | v1.447.0 |
|---|---|---|---|---|
| 1 | I want Jarvis to work on my GitHub repositories. | file tools; ask `app_connect`; Connect GitHub | ✓ | ✓ |
| 2 | Can you look at my Outlook calendar … Thursday? | ask `app_connect`; Connect Microsoft 365 | ✓ | ✓ |
| 3 | Pull the latest invoices from my Google Drive folder called Clients 2025. | Connect Google Drive; `web_search`/`web_fetch` still auto-armed ("latest") | ◐ | ◐ |
| 4 | Post a note in our Slack #tax-team channel when … ready. | channel tools + `app_connect`; Connect Slack | ✓ | ✓ |
| 5 | Save these meeting notes to my Notion workspace. | Connect Notion; memory tools | ✓ | ✓ |
| 6 | Open the IRS website and download the 2025 Form 1040 instructions. | web tools | ✓ | ✓ |
| 7 | Read the files in C:\Clients\Acme … tax returns. | file tools | ✓ | ✓ |
| 8 | Click the Submit button in the QuickBooks desktop app for me. | nothing armed; `desktop_control` line | ✓ | ✗ |
| 9 | Use Claude for this chat instead of the default. | settings tools; `model_provider` door (its line says the model menu picks for one chat) | ✓ | ✗ |
| 10 | I have a ChatGPT Plus plan, can you use that to answer? | settings tools; `model_provider` door | ✓ | ✗ |
| 11 | Here is my OpenAI key, use GPT for answers. | settings tools (secure card); `model_provider` door | ✓ | ✓ |
| 12 | Set up Gemini so I can try Google's model. | settings tools; `model_provider` door (web tools too) | ✓ | ✗ |
| 13 | I'm running Ollama with llama3 on this PC, use it. | ask `model_server_add`, `model_server_default` | ✓ | ✗ |
| 14 | LM Studio is running on port 1234, connect to it. | `model_server_add`/`_default`, but `app_connect` too | ◐ | ✗ |
| 15 | Use OpenRouter for the big questions. | settings tools; `model_provider` door | ✓ | ✗ |
| 16 | Add the filesystem MCP server from the Claude docs. | ask `mcp_server_add` (+ health readers) | ✓ | ✗ |
| 17 | Hook up our firm's internal pricing API at https://api.vrtax.local/v1 … | ask `web_service_add` | ✓ | ✗ |
| 18 | Let the Jarvis on my office PC take overnight jobs from this one. | `another_computer` line | ✓ | ✗ |
| 19 | Which local models can run on this laptop? | `config_list`; `local_model` step | ✓ | ◐ |
| 20 | Every night at 11 run the tests in C:\Projects\ClosePilot and fix what breaks. | `night_task_add` (+ list/cancel/move) beside schedule tools | ✓ | ◐ |
| 21 | Add my DGX Spark at http://192.168.1.50:8000 as a model server. | `model_server_add`/`_default` | ✓ | ✓ |
| 22 | Switch chat to my work Claude account. | settings tools only; no setting holds a chat's account, no door to the model menu's account entries | ◐ | ◐ |
| 23 | Point your long-term memory at my Obsidian vault in D:\Notes. | ask `memory_base_add` | ✓ | ✗ |
| 24 | For the ClosePilot folder, always use Codex in Auto mode. | ask `build_defaults_set` (+ team/show) | ✓ | ✗ |
| 25 | Have my coding sessions run with a reviewer that checks every change. | ask `build_team_set` | ✓ | ✗ |
| 26 | Don't start Jarvis when Windows boots. | settings writers | ✓ | ✓ |
| 27 | Answer me in Spanish from now on. | settings + `remember_preference` | ✓ | ✓ |
| 28 | Create a persona called Tax Partner who writes formally. | persona tools | ✓ | ✓ |
| 29 | Stop reading my replies out loud. | settings writers | ✓ | ✓ |
| 30 | What can you do with my email? | Guide tools; one mail-choice door | ✓ | ✓ |
| 31 | Can you work with QuickBooks Online? | Guide tools armed (+ web) | ✓ | ✗ |
| 32 | Help me write a small Python tool that renames PDFs by client name. | coding offer → Chat › Code | ✓ | ✓ |
| 33 | What is the Build page for? | Guide tools | ✓ | ✓ |
| 34 | My outlook on the Q3 numbers is grim, help me write the memo. | nothing | ✓ | ✓ |
| 35 | The client wants to switch from Slack to Teams, draft a pros and cons list. | no door, but channel + `app_connect` ask-armed | ◐ | ◐ |
| 36 | Schedule a call with the Johnson family for next Tuesday. | nothing | ✓ | ✓ |
| 37 | Remind me how depreciation schedules work for a vehicle. | nothing | ✓ | ✗ |
| 38 | I'm keeping my notes in a notebook; summarize this text. | memory readers only | ✓ | ✓ |

**Old 38: 34 ✓ · 4 ◐ · 0 ✗** (v1.447.0: 21 / 5 / 12). Every v1.447.0 miss is fixed; the four ◐ are noise beside a right answer (3, 14, 35) or a thing no setting holds (22).

### 30 new everyday sentences

| # | Sentence | Now | Verdict |
|---|---|---|---|
| 1 | Connect my Gmail so you can read my email. | `app_connect`; Connect Gmail + `gmail_setup` step | ✓ |
| 2 | I want to use my Claude Pro subscription with Jarvis. | NOTHING (also "I want to use my ChatGPT subscription with Jarvis") | ✗ |
| 3 | Can you text me on Telegram when a long job finishes? | `channel_connect`/`channel_toggle` | ✓ |
| 4 | Send me a Slack message every morning with what's on my calendar. | schedule tools + channel tools ✓, but Slack offered as an APP (Connect Slack + `app_connect`); the calendar (none connected) gets no door | ◐ |
| 5 | Every Monday at 9am, summarize last week's new files in my Clients folder. | `schedule_create` + file readers | ✓ |
| 6 | Whenever a new PDF shows up in my Downloads folder, rename it by client name. | `reflex_create` (no file source exists) + download tools; `watcher_suggests` line not fired | ◐ |
| 7 | Watch my Documents\Invoices folder and tell me when something changes. | `sentinel_add` (+ watcher settings) | ✓ |
| 8 | When I get an email from the IRS, forward it to my phone. | channel tools only; no `reflex_create` for the email rule | ◐ |
| 9 | Only let Jarvis look at C:\Clients. | file readers only; `readable_folders` writer NOT armed (also "Only let Jarvis read C:\Clients") | ✗ |
| 10 | Fix the failing test in my ClosePilot repo. | file tools; NO coding door ("Fix the failing test in my repo" gets it) | ◐ |
| 11 | Add my second Claude account so I can switch when I hit the limit. | `account_add`; `accounts` door | ✓ |
| 12 | Use my Notion workspace as your long-term memory. | `memory_base_add` (+ Notion connect door) | ✓ |
| 13 | Remember that my firm's fiscal year ends in June. | `ltm_append`/recall | ✓ |
| 14 | Use the local model on my DGX box instead of the cloud from now on. | settings writers + `remember_preference` | ✓ |
| 15 | Keep this chat on this PC, nothing to the cloud. | `chat_local_only` | ✓ |
| 16 | Connect Dropbox. | `app_connect`; Connect Dropbox | ✓ |
| 17 | Set up the browser extension in Edge. | `browser_get_status` + settings tools; no add-on door ("Install the browser add-on" gets it) | ◐ |
| 18 | Can you control my mouse and fill in the form in Drake Tax? | `desktop_control` line ✓, but `pdf_form_fill`/`pdf_form_fields` armed | ◐ |
| 19 | Stop all background jobs right now. | settings writers (Emergency stop) | ✓ |
| 20 | Undo the last change you made. | `change_undo`, `config_undo` | ✓ |
| 21 | Back up my Jarvis data to my OneDrive folder every night. | settings writers + `jarvis_backup` line | ✓ |
| 22 | Make a new project for the Smith family in D:\Clients\Smith. | `project_create` | ✓ |
| 23 | Create an agent that reviews my tax workpapers. | `agent_create` | ✓ |
| 24 | Install Codex for me. | `shell` card + settings tools + `model_provider` door; not the one-press install job | ◐ |
| 25 | Add the GitHub MCP server. | `mcp_server_add` + `app_connect` (GitHub is a catalog app — either is right) | ✓ |
| 26 | My schedule is packed this week, help me prioritize. | schedule writers ask-armed (misfire, carded) | ✗ |
| 27 | The project manager said the Smith project is late; draft an apology email. | nothing | ✓ |
| 28 | Write a memo about why the firm should move to the cloud. | `write_document` (a memo) — no settings/egress writer | ✓ |
| 29 | What's a good default answer when a client asks about extensions? | nothing | ✓ |
| 30 | Our office printer keeps disconnecting from the network; any ideas? | nothing | ✓ |

**New 30: 20 ✓ · 7 ◐ · 3 ✗.** Requests 1-25: 16 ✓ / 7 ◐ / 2 ✗; near-misses 26-30: 4 ✓ / 1 ✗.

Extra (not counted): "Schedule time to review the Smith return" also ask-arms schedule writers; "What are Watchers for?", "What is the Rules page?", "What is the Fleet/Artifacts page?", "How do I make a rule?", "Where do I change the theme?" arm no Guide (old names missed before v1.452.0 too, so not a regression); "I have Claude Pro, use it", "Use my Claude subscription for answers", "Can you use my Claude Pro account?" all work.

---

## 3. Category scores

**Discoverability — 85** (v1.447.0: 76 in the results; this lane's own 78). Wave A closed every miss of the baseline's 38 in code (34 right, 0 miss), and the model/provider axis that held it — the one place a beginner most needs a door — now leads to Connections or a model-server card for every vendor. Configuration from chat covers settings, destinations, apps, generic MCP servers, web services, model servers, accounts, memory bases, folder defaults, teams, the night shift, projects, agents, personas, language and Local only. Held under 90 by everyday phrasings a fresh set still finds: the most common plan sentence ("I want to use my Claude Pro subscription with Jarvis") arms nothing; folder limits phrased "only let Jarvis look at <path>"; rules about files arriving (no file source, no honest line); an email→phone rule without the word "rule"; a coding door that needs "my repo" with no name; feature questions about pages other than Build/Code; and the person's own schedule arming schedule writers.

**Trust — 92** (v1.447.0: 86). All eleven K items pass. Every nervous point v1.447.0 named is closed: a tool runs unasked only after the user's own "Allow for this chat" (the per-tool picker has no renderer) and the Tools menu says so in true words; setting cards show option names and link to the row; the chat list says "Needs you"; the approval card has "No, tell it what to do instead" and says the box comes back; safety cards never reach the phone from any lane; the website risk door cards and resumes; Build › Code says where its model answers. Held under 95 by: agent-run and Build › Code asks still carry no "why"; "Iron-Proxy" in daemon sentences on coding screens; the `model_provider` door beside an already-connected vendor; misfires that put an unexpected "update a schedule" card in front of a user; "where" only on Build › Code's Session tab; `memory` missing from `SAFETY_REASONS`.

---

## 4. Remaining work (impact × frequency × confusion)

1. **"I want to use my Claude Pro / ChatGPT subscription with Jarvis" reaches nothing.** `USE_VENDOR`'s `ANSWERS_TAIL` (`setup_words.py:325-335`) does not take "with Jarvis / with you / in this app". Add it (and to `USE_PLAN`). **S** · discoverability (the owner's audience types exactly this).
2. **File-arrival rules point at a tool that cannot do it.** "Whenever a new <file> shows up / arrives / lands in my <folder>, <act> it" arms `reflex_create` (sources webhook/comm/email/calendar/slack only) and misses `watcher_suggests`. Let `watch_and_act` (`setup_words.py:173`) read the "whenever … shows up in my <folder>" shape, and keep `reflex_create` out when the source is a folder. **S** · trust + discoverability.
3. **The person's own schedule arms the schedule writers** ("My schedule is packed", "Schedule time to review …"). `records._schedule_noun_hit` (`records.py:199`): "my/our schedule" + is/was/'s, and "schedule time/a meeting/a call to …", are the person's. **S** · trust (a surprise card).
4. **"Only let Jarvis look at / read <path>" misses `readable_folders`.** A trigger shape for a path (not only "my Documents folder"). **S** · discoverability (a CPA's first privacy request).
5. **"When I get an email from <X>, forward it to my phone" never offers the rule.** `reflex_create` arming for "when I get an email/message from …" + an action. **S**.
6. **Install from chat.** "Install Codex / Claude Code for me" → Connections door + a `shell` card; give it a `cli_install` page step with the install press (`lib/cliInstall.ts` job) as the sign-in steps have. **S/M**.
7. **Coding door misses "my <Name> repo".** `_REPO_TASK_RX` (`autoselect.py:~3740`). **S**.
8. **Guide misses feature questions about Rules / Watchers / Model servers / What agents made** and "How do I make a rule?". `_FEATURE_QUESTION` rows from `lib/nav` labels + aliases. **S**.
9. **"Set up the browser extension (in Edge)" gets no add-on door** — a deliberate trade-off ("any extension"); accept "the browser extension" when it opens the sentence or the user names Edge/Chrome with "set up/install". **S**.
10. **Trust leftovers:** `card_why` on agent-run asks (**S**); `memory` into `SAFETY_REASONS` (**S**); `model_provider` door only when that vendor is not connected (**S**); Iron-Proxy words in `harness/build_accounts.py` / `hosted/words.py` (**S**); noise arming (`pdf_form_fill` on "fill in the form in <desktop app>", `app_connect` beside a destination Slack and beside LM Studio, web tools on "latest" beside a Drive door) (**S** each).

## 5. Ease-of-use items in this lane (docs/EASE-OF-USE-REVIEW-2026-11-07.md)

| Item | State | Where |
|---|---|---|
| 23 Emergency stop invisible / two names | done | `EmergencyStopStrip.tsx` (v1.446.0) |
| 34 Can't type during a card / no "do this instead" | done | `ApprovalCard.tsx:185,263` `onRedirect`, `lib/dockAsk.ts:119` `BOX_COMES_BACK_NOTE`, `page.tsx:13884` |
| 35 Local only applies to every new chat | done | `chat_new_local_only` (per chat) |
| 36 Setting cards show internal values | done | `settings/tools.py:230,532-547`, `lib/configCards.ts:142` |
| 37 Enter during a reply = steer note | done (explained) | `lib/composerChips.ts:182` key hint, steer placeholder |
| 38 Escape doesn't close composer menus | done | Wave D3 (capture-phase Escape) |
| 39 Waiting vs unread by colour only | done | `ThreadGroups.tsx:470` "Needs you" |
| 44 Greyed buttons explain only on hover | done (main presses) | `lib/whyDisabled.ts`, `components/DisabledNote.tsx` |
| 46 Small errors look like a broken reply | done | `page.tsx:4235-4259` `chat-notice` + live region |
| 61 Nothing says what to try next | done | `HomeReadiness.tsx` `NextStepLine` |
| 66 Spotlight "Task done" for needs-you | done | `desktop/main.js:3018,3042` |

All 11 done (v1.447.0: 2 done).

## 6. Regressions

None found. Checked: the v1.452.0 page renames (Watchers, Rules, What agents made, Model servers) did not change any matcher (feature questions about those pages missed before too; "Turn off the watchers" still arms the watcher settings and `sentinel_*`; "Add a rule that pings me when a Slack message mentions me" arms `reflex_create`); the owner's item-9 decision changed no arming path (the per-tool picker was already unrendered). The `uv run` of the probe rebuilt the editable install in `.venv` (not a repo change).

## 7. Never proven live

- Every probe ran the MATCHERS in-process; no model answered any of the 68 sentences (or the variants), so whether a real model uses the door/line it was given is unproven.
- The Tools menu's `ARMED_TOOLS_RUN_UNASKED` line, the redirect field, "Needs you", the setting card labels and Build › Code's "Where it works" were read in the dashboard code, not opened in the packaged app, a real browser or at phone width.
- `mcp_server_add` / `web_service_add` / `model_server_add` cards were not driven against a live service.
- Nothing was sent to the live daemon on 8787.

---

# Re-audit lane G + N — Error & recovery UX, Stability (v1.452.0, commit 80ec5e89, 2026-11-08)

Read-only. Every mark is from the current code (file:line). Nothing was run against the live app or the suites. CI was read with `gh run list/view` only. Baseline: `docs/PRODUCT-READINESS-LANES-v1447.md`, lane G + N (v1.447.0, 9cf4a637).

## 1. Failure traces, redone against the current code

| Failure × surface | Where shown | Plain? | Detail folded? | What failed / not affected | Auto recovery shown? | Action offered | Work continues? | Move vs v1.447.0 |
|---|---|---|---|---|---|---|---|---|
| Provider down / 5xx / timeout: **chat** | `chat-turn-error` line under the composer (`app/chat/page.tsx:13444`), `title. fix` from `chat_turn.error_payload` → `failure_words` | Yes | Yes: Details toggle (`page.tsx:13615-13632`) | Provider named; message back in the box (`DRAFT_BACK_WORDS`, `page.tsx:13670`) | Router retries not shown; no patience in plain chat (`harness/patience.py:38`, by design) | Retry with cooldown, Choose another model (`RetryTurnButton`, `page.tsx:13575`), Retry with the default | Yes | Same |
| Rate limit / account limit: chat | Same line, by-kind sentence | Yes | Yes | Yes | Cooldown on Retry | Retry / choose model | Yes | Same |
| Signed out, Claude Code / Codex: chat | Headline + Sign in press | Yes | Yes | Yes | n/a | Sign in press | Yes | Same |
| Signed out, **Grok**: chat and Chat › Code | Headline + **Sign in press** now: `failureActions.cliOf` → `signInCliFor` (`lib/failureActions.ts:96-98`), list in `lib/signInClis.ts` (Claude Code, Codex, Grok) | Yes | Yes | Yes | n/a | **Press** | Yes | **Fixed** (was sentence only) |
| Wrong key (401/403): chat | "X refused your key…" + Update the key link | Yes | Yes | Yes | n/a | Link | Yes | Same |
| Any failure: **Chat › Code** | On the reply (`components/harness/HarnessFailure.tsx`), Sign in, key link, Continue from where it stopped, Retry, Details; patience shown (`HarnessSession.tsx:3390`, `harness-provider-wait`) | Yes | Yes | Yes | **Yes, shown** | Yes | Yes (kept rounds) | Same |
| Local only refusal: **Chat › Code** | Now **Choose a local model** and **Send to <model> once** on the reply (`HarnessFailure.tsx:43, 174, 207, 215`), no Retry (`action.kind !== "local_only"`), Send once hidden when the chat is held | Yes | n/a | Yes | n/a | **Two presses** | Yes | **Fixed** (was sentence + a Retry that refused again) |
| Any failure: **Build › Code** | `code-error` row (`components/buildCode/CodeConversation.tsx:566`): title, fix, Details, Sign in, Retry (`650`), Continue after a cut (`638`), limit waiting (`572`), carry-on row (`493`); the receipt now leads with ⚠ for a failed/stopped reply (`receiptOutcome`, `692-700`) | Yes | Yes | Tool named; files changed counted on the receipt | **Yes** | Yes | Yes | Better (receipt outcome, v1.449.0) |
| Provider down: agent run / mission | Run page "The run failed: …", Details (`failure_detail`); RunResultCard; kanban | Yes | Yes | Yes | None (by design) | Rerun / Continue | Yes | Same |
| Provider down: schedule fire | Schedules row + bell + tray | Yes | Via run page | Yes | None | Open run | Yes | Same |
| Phone, browser sidebar | Plain lines, sidebar folds detail | Yes | Sidebar yes | Yes | n/a | Resend | Yes | Same |
| Extension fails to start | Tools row "Didn't start: …" + fix + last output + Retry; bell | Yes | Yes | Yes (one pack) | n/a | Retry / edit | Yes | Same |
| Workflow started by a schedule/rule runs away | **Stop on every live Run history row** (`app/workflows/page.tsx:662-684, 822-840`, POST `/workflows/runs/{id}/cancel`; title says a waiting question is dropped); listed in Running now (`lib/runningNow.ts:66, 362-375`) | Yes | n/a | Yes | n/a | **Stop** | n/a | **Fixed** (was canvas-only) |
| Chat list read fails | "Your chats couldn't load. Try again." (`page.tsx:4457, 12160-12167`, `lib/loadFailed`) | Yes | n/a | Yes | n/a | Try again | Yes | **Fixed** (was a blank rail) |
| Small chat errors (upload, remember, rename …) | Own dismissible `chat-notice` + live region (`page.tsx:4235-4259, 13326, 13412-13419`) | Yes | n/a | Yes | n/a | × | Yes | **Fixed** (shared the turn-error line) |
| Removing the default model's server / disconnecting the default | `daemon/default_replacement.py`: named before the press (`DefaultImpact`), a working replacement written through the settings writer with Undo, else the demo with a warning | Yes | n/a | Yes | Yes, said | Undo | Yes | **Fixed** (ease 47) |
| Update fails to start / window crash / Spotlight / startup | `RELEASES_PREVIOUS_HOW` names the page, the version and the file (`desktop/main.js:2282, 6127`), raw gate error to the log; `recoveredWords` (`main.js:6372-6375`); `spotlightErrorWords` (`3115-3150`); `STARTUP_LOG_LINE` (`6107`); splash second line at 15 s (`loading.html:125-126, 444`) | Yes | Raw to the log | Yes | Window reload said | Open logs / Releases page | Yes | **Fixed** (raw text, "few seconds" for 3 min) |
| A press that answers **5xx** (Fleet save, Skills apply, Artifacts run, Telegram "find chat id", Iron-Proxy routes …) | ErrorNote shows `ApiError.message` = the 500 envelope `"internal error [err_xxxx]: KeyError: …"` (`daemon/auth.py:637`; `lib/api.ts:214-223`; e.g. `app/fleet/page.tsx:1055`, `app/skills/page.tsx:226`, `app/artifacts/page.tsx:137`; `routes/comm.py:999` "could not reach Telegram: <exc>"). **Also** `lib/api.ts:211` raises the app-wide banner for ANY ≥500, POST included: "Part of this page didn't load. It will try again in a moment." (`lib/offlineWords.ts:41`, `components/DaemonBanner.tsx:509-540`) — untrue for a press, and a remote 502 reads as the app breaking | **No** for 500s | **No** (raw, inline) | Partly | n/a | None beyond the banner's Reload | Yes | Same code; stricter reading (the banner on a POST is newly noticed) |
| Chat turn unwatched for 1 h (orphan guard) | `stop_predicate` (`daemon/durable_turns.py:963-972`) stops it; no reason frame is written; a turn parked on an unanswered card is ended too (the bell had listed it, `waiting_unwatched` 795) | — | — | — | — | — | Partial kept, no word why | Same (open) |
| Daemon down / restart mid-turn | DaemonBanner states; "stopped while answering … press Retry" record; code replies carried on once | Yes | n/a | Yes | Yes | Restart / Retry | Yes | Same |
| Route-level React error | `app/error.tsx:25-35`: plain card ("The rest of Iron Jarvis is still running") but the raw `error.message` + digest is ALWAYS shown in a `<pre>`, and it says "head back to the dashboard" | Mostly | **No** (forced) | Yes | n/a | Try again / Go home | Yes | Same (newly noticed) |

**Does backend recovery reach the frontend?** Yes, everywhere it did at v1.447.0, plus: default replacement on removal is said before and after, workflow runs are visible and stoppable, a failed chat list says so. The quiet cases left: Iron-Proxy account rotation in chat (the receipt names the account that answered, not the one skipped, `iron_proxy/accounts.py` unchanged) and the orphan-guard stop.

## 2. Headline

| Section | v1.447.0 | v1.452.0 |
|---|---|---|
| G. Error and recovery UX (10) | 8 PASS / 2 PARTIAL / 0 FAIL | **8 PASS / 2 PARTIAL / 0 FAIL** (both PARTIALs narrower) |
| N. Stability / product confidence (14) | 11 PASS / 3 PARTIAL / 0 FAIL | **14 PASS / 0 PARTIAL / 0 FAIL** |
| Release gate (last 15 runs) | — | 15 of 15 green or cancelled duplicates (13 success, 2 cancelled master-push duplicates of a tag push); no "passed only on retry" annotations on the last 5 |
| Category: Error/recovery UX | 87 | **91** |
| Category: Stability | 92 | **95** |

## 3. Per-item marks

### G. Error and recovery UX: 8 PASS, 2 PARTIAL, 0 FAIL

| # | Item | Mark | Move | Evidence | Experience | Missing / smallest fix / size |
|---|---|---|---|---|---|---|
| G1 | Error appears where the user was working | PASS | = (better) | Chat `page.tsx:13444`; Chat › Code `HarnessFailure.tsx`; Build › Code `CodeConversation.tsx:566`; small errors now their own `chat-notice` (`page.tsx:13412`); Run history Stop errors on the row (`workflows/page.tsx:681`) | In place on every surface; small failures no longer look like a broken reply. | — |
| G2 | Understandable language | PASS | = (better) | `failure_words`; `hosted/words.py:543-555`; desktop dialogs plain (`main.js:2282-2284, 6107, 6372, 3115`) | The three desktop raw-text spots of v1.447.0 are gone. | Press failures that answer 500 still print the envelope `internal error [err_…]: KeyError: …` (`auth.py:637` → `api.ts:219` → ErrorNote; the `e instanceof ApiError ? e.message : String(e)` pattern is in 89 files). Fix: in `api()` turn a 500 envelope into "Iron Jarvis hit a problem doing that (err_xxxx). Try again." with the class name moved to `technical` (S). Not enough to fail: the common paths are worded. |
| G3 | Technical detail available, not forced | PASS | = | Chat Details toggle `page.tsx:13615-13632`; harness `FailureDetails`; Build › Code `<details>`; run page `failure_detail` | Folded on every chat/code surface. | `app/error.tsx:30-35` always shows the raw message + digest (fold it under Details, say "Go home" not "dashboard") (S). Stale comment `lib/failureLine.ts:16, 61` still says "hover title only". |
| G4 | Identifies what failed | PASS | = | Provider, tool, pack, step, account, rule (`reflex_failed_title`, `notifier.py:382`) named | — | — |
| G5 | Identifies what was NOT affected | PARTIAL | = (narrower) | Chat `DRAFT_BACK_WORDS`; kept rounds; Build › Code receipt now lists files changed even on a failed reply (`CodeConversation.tsx:692-700`); route error boundary "The rest of Iron Jarvis is still running" | Most lines say what is kept. A failed agent run's headline and a failed Build › Code reply still never say "the files it wrote so far are kept" (`hosted/words.generic_error` 543-555 unchanged; `orchestrator._finalize_failed` unchanged). | One kept-files clause when the ledger lists created/changed paths (S). |
| G6 | Safe automatic recovery when appropriate | PASS | = | Patience (`harness/patience.py`), carry-on, limit wait, desktop self-restart, restore give-up, default replacement on removal | — | Optional plain-chat wait for 529 (M). |
| G7 | User sees that recovery occurred | PASS | = | `harness-provider-wait`, `code-carry`, `code-limit-waiting`, `default_replaced` said on the card and in chat | — | Iron-Proxy rotation in chat still unsaid (S, rare). |
| G8 | Clear action when the user must act | PASS | = (gaps closed) | Grok press `failureActions.ts:96-98` + `signInClis.ts`; Local only presses `HarnessFailure.tsx:207-215`; Run history Stop; Try again on the chat list | Both v1.447.0 gaps closed in code. | A 5xx press failure offers nothing but the global Reload banner. |
| G9 | After repair, original work continues | PASS | = | Message back + Retry; kept rounds Continue; `code-continue-cut`; Send once re-sends the reply's question | — | — |
| G10 | Never silently abandons work | PARTIAL | = (narrower) | Fixed: workflow runs now stoppable and visible (`workflows/page.tsx:662-840`, `runningNow.ts:362`); reflex/watcher/part-failed rows; chat list failure said. Open: orphan guard `durable_turns.py:963-972` writes no reason; a chat turn parked on an unanswered card is ended after an hour unwatched | One quiet end left: a chat turn nobody watched for an hour stops, its partial reads as a finished (stopped) reply with no "why". | Write an `INTERRUPTED_DETAIL`-style error frame ("stopped because nobody was watching for an hour; what it wrote is kept") at the orphan stop, and never orphan-stop a turn parked on a card (S). |

### N. Stability / product confidence: 14 PASS, 0 PARTIAL, 0 FAIL

| # | Item | Mark | Move | Evidence | Experience | Missing / fix |
|---|---|---|---|---|---|---|
| N1 | Survives UI navigation | PASS | = | Durable turns, `lib/turnCache`, `paneHost` parking | — | — |
| N2 | Survives reload / window close | PASS | = | Resume by cursor; drafts on `pagehide`/unmount (`page.tsx:10936-10953`); Build › Code TailLog | — | — |
| N3 | Survives app restart | PASS | = | `tests/test_update_rehearsal_v1338.py`, `tests/test_code_host_rehearsal_v14220.py`, carry-on | — | Never proven in the packaged app (§7). |
| N4 | Background work visible after returning | PASS | **PARTIAL → PASS (code)** | `lib/runningNow.ts:30-33, 66-68, 362-395`: workflow runs, media jobs, running night tasks; `components/RunningNow.tsx:87-89` | Everything running is in one popover. | — |
| N5 | Agent state unambiguous | PASS | = | `open_steps`, `keeps_working`, `statusWords` one table (scan pin `status-words-one-table-v1449`) | — | — |
| N6 | Duplicate work prevented | PASS | = | One writer per conversation; carry-on once; turn ids followed; risk door Allow consumed once (v1.449.0) | — | `ComputerUseHarness.run` still approves without consuming (Wave B OPEN; standalone loop, low reach). |
| N7 | Failed work has an understandable state | PASS | = | `statusWords`, `completed_with_failures` (`workflows/engine.py:87, 688`), Spotlight outcome words (`main.js:3018-3042`) | — | — |
| N8 | Tool failures don't poison unrelated work | PASS | = | Registry failed rows, per-call MCP fence, per-pack isolation | — | — |
| N9 | Remote failures don't make the whole app look broken | PASS | **PARTIAL → PASS (code)** | Chat list `page.tsx:12160`; Sessions agent select a disabled placeholder until `/agents` settles (`components/NewSessionForm.tsx:84, 283`); remote Test worded | Both v1.447.0 gaps closed. | Stricter residual: `lib/api.ts:211` raises "Part of this page didn't load. It will try again in a moment." for ANY 5xx incl. a POST press or a remote 502 (Telegram, Iron-Proxy, a git draft): the words are untrue for a press and make one remote's failure read as the page. It clears on the next good request. Fix: signal only for GET (S). |
| N10 | Long-running work manageable | PASS | **PARTIAL → PASS (code)** | Run history Stop (`workflows/page.tsx:822-840`, "Stopping" via statusWords); harness Stop/Stop the step; Build › Code interrupt; night Cancel | — | — |
| N11 | Resource exhaustion controlled | PASS | = | Pool cap, log bound, OCR child, no WMI | — | — |
| N12 | Processes cleaned up | PASS | = | Jobs, code host bounds, sweeps | — | Documented limits unchanged. |
| N13 | Updates don't destroy important state | PASS | = (words fixed) | Rehearsal; safety backup before restore (`routes/settings.py:720-788`); upgrade-failed dialog names page + file (`main.js:2282, 6127`); splash line at 15 s | — | — |
| N14 | Recovery understandable | PASS | = (better) | `WHY_*`, `GAVE_UP_NOTE`, `recoveredWords` | — | `recoveredWords("oom")` still says "Nothing was lost" (`main.js:6373`); a half-typed message is saved only on `pagehide`/unmount (`page.tsx:10942-10953`), which a renderer OOM skips. Say what the other branch says (S). |

## 4. CI / stability record

- **Release (release.yml), last 15:** all green; the 2 "cancelled" are master-push duplicates of a tag-pushed release (v1.446.1, v1.445.1), by design. No v1.448–v1.452 release went red; no "passed only on retry" warning annotation on the last 5 gates. v1.452.0 published 3 assets.
- **Tests (tests.yml), last 15:** 5 red, every one the FIRST branch run of a wave (A, B, C, D, the owner's answers), each followed by a green run. Classified:
  - Stale cross-file pins after word changes (most): connections words, project-page coding sessions, `chat-layout-v1215` / `chat-wave3-v1311` ("multiple elements: Start a new conversation" — a REAL duplicate New-chat door, fixed as v1.450.1 "one New chat door"), `connections-calm-k2` spaced dashes, `test_page_doors_v14360` (grok-cli press not in the dashboard list).
  - Real defects caught before release: `test_fix_cu_approval::test_injected_resolver_true_still_executes` (wave B's risk door broke an injected resolver), `test_guide_v1224::...vocabulary_for_memory_base` (fixed v1.450.1 "definitions from…"), the timings-file check (fixed v1.448.1).
  - A real test race: `folder-defaults-v1426-0` (failed after retry; Send landed before the git read under item 43's new `branch: false` rule) — fixed test-only in 80ec5e89.
- Pattern: the lanes run only touched files; the branch gate is where cross-file pins and real seams are caught — it is working as the gate, and nothing red reached master. The `--reruns 1` / `--retry=1` policy did not hide anything visible in the annotations.

## 5. Ease-of-use review items in these sections

| Ease # | Item | State |
|---|---|---|
| 3 | Wrong API key becomes default untested | Done (v1.445 `?check=1`; v1.450 Settings and the card test first) |
| 4 | "Not signed in" for 2 min after signing in | Done (v1.445 check-now + sign-in watch) |
| 7 | Restoring a backup can't be undone | Done (`-before-restore` safety backup) |
| 13 | Editing a destination quietly changes it | Done (v1.446.0) |
| 20 | Failed rule tells nobody | Done (`reflex.failed`) |
| 21 | Watcher suggestions silent | Done (`autonomy.proposed` quiet bell row) |
| 22 | Workflow with a failed step says Done | Done (`completed_with_failures`) |
| 23 | Emergency stop invisible | Done (`EmergencyStopStrip`) |
| 30 | Running workflow stoppable only from the canvas | Done |
| 31 | Step editor swallows JSON | Done (`jsonProblem`, `NodeInspector.tsx:207-209`) |
| 41 | Chat list looks empty on failure | Done |
| 46 | Small errors look like a broken reply | Done (`chat-notice`) |
| 47 | Removing the default leaves chat pointing at nothing | Done (`default_replacement.py`) |
| 49 | Ollama running with no model | Done (v1.450.0 door states) |
| 57 | Memory base "added" unchecked | Done (read back) |
| 66 | Spotlight "Task done" when it needs you | Done |
| 67 | Upgrade-failed dialog | Done |
| 68 | Desktop raw technical text | Done; OOM line still "Nothing was lost" (partly) |
| 69 | Splash "a few seconds" | Done |
| 70 | Activity raw event names | Done |
| 71 | Agent history duplicates | Done (file stem); same stem in two project folders still two rows, one id (Wave B note) |
| 72 | Sessions React error | Done |
| 74 | Build stuck states | Done (`lib/buildStuck.ts`, `DETECT_BOUND_MS` 20 s) |

## 6. Remaining work (impact × frequency × confusion)

1. **5xx press failures print the 500 envelope, and every 5xx (POST included) raises "Part of this page didn't load. It will try again in a moment."** — `daemon/auth.py:637`, `lib/api.ts:211-223`, `lib/offlineWords.ts:41`; 89 files use the raw-message catch. Fix in ONE place: `api()` words a 500 envelope ("Iron Jarvis hit a problem doing that. Try again. (err_xxxx)", class name to `body`/technical) and signals the banner for GET only. S. Medium frequency (every unexpected bug surfaces this way), high confusion.
2. **Orphan-guard stop of an unwatched chat turn says nothing** — `durable_turns.py:963-972`. Write a reason frame; do not orphan-stop a turn parked on a card (asks wait forever since v1.247.0). S.
3. **G5: failed runs / failed Build › Code replies don't say their files stayed** — `hosted/words.generic_error` 543-555, `orchestrator._finalize_failed`. One clause from the ledger. S.
4. **Route error boundary forces raw text** — `app/error.tsx:30-35`: fold under Details, "Go home". S.
5. **OOM reload "Nothing was lost"** (`main.js:6373`) and **Iron-Proxy rotation in chat unsaid**. S each, rare.
6. Stale comment `lib/failureLine.ts:16, 61` (docs only).
7. `ComputerUseHarness.run` approves without consuming (one Allow, two clicks, standalone loop). S.

## 7. Regressions

None found. No v1.447.0 PASS in G or N came undone. Checked specifically: v1.452.0 item 43 (Chat › Code sends `branch: false` while the folder's git facts are unread; "Keep these changes" commits then merges and says both on a refused merge) — no lost work path; Running now's new reads (`/media/jobs` 503 only when `platform.media_jobs` is None, which `platform.py:1731` always sets) — no new banner trigger; the risk door's cancelled wait now denies its queue row (v1.449.0). The global-banner-on-POST behaviour and the forced raw text in `app/error.tsx` are older than v1.447.0, newly noticed here, not regressions.

## 8. Never proven live (these areas)

- The packaged app for any of waves A-D and v1.452.0: the update rehearsal, code-host restart survival and carry-on run only against a SOURCE daemon; the desktop's new dialogs (`RELEASES_PREVIOUS_HOW`, `recoveredWords`, `STARTUP_LOG_LINE`), the 15 s splash line and the Spotlight outcome words were never seen in the installed app.
- Default replacement on a real server removal; the safety backup on a live restore.
- Patience against a real overloaded provider; a real account limit waited out and a group move; Codex between two ChatGPT accounts.
- Tray toasts for `reflex.failed`, part-failed workflows, the night summary; the Emergency stop strip on a real stop.
- A screen reader hearing "Reply ready." / `chat-notice-live`.
- The orphan-guard stop after a real hour; a stuck Store-pwsh restore given up at 60 s on the user's PC.

---

# Re-audit lane H-I-M — v1.452.0 (commit 80ec5e89), 2026-11-08

Read-only. Every mark is from the CURRENT code. Method: file reads and grep,
plus one read-only scan that transpiles the repo's own `readableCopy` walker
(`dashboard/__tests__/helpers/retiredWords.ts` + `dashGuard.ts`) with
`typescript.transpileModule` into the scratchpad and runs it over
`dashboard/app`, `components`, `lib` (no `__tests__`) with 29 engine-room
terms (MCP, transport, SSE, stdio, inference, provider, harness, runtime,
daemon, context window, Tailscale, JSON, environment variable, PATH, endpoint,
Iron-Proxy, worktree, cron, fleet, CLI, OAuth, sentinel, reflex, artifact,
true/false, base URL, node, schema, token): 328 prose hits (v1.447.0: 473 with
a slightly different term set), every non-"token" hit read. The settings
schema's non-advanced rows were listed by importing
`iron_jarvis.settings.schema` (82 rows, read one by one). Daemon sentences
checked by grep of string literals / `detail=`. Nothing was driven in the
running or packaged app; no test suite was run; no repo file was changed.

Sections: H (15 items), I (14 items), M (8 items).

---

## 0. Headline

| Section | v1.388.0 (0g) | v1.447.0 | **v1.452.0** | Move |
|---|---|---|---|---|
| H. Consistency | 9 / 6 / 0 | 5 / 10 / 0 | **10 / 5 / 0** | +5 PASS (H4, H10, H11, H13, H14) — all code changes |
| I. Hide the engine room | 11 / 3 / 0 | 10 / 4 / 0 | **12 / 2 / 0** | I12, I13, I14 to PASS (code changes); I1, I6 stay PARTIAL (smaller) |
| M. "What does this mean?" | 2 / 6 / 0 | 0 / 8 / 0 | **1 / 7 / 0** | +1 PASS (M3) — code change; every other M row smaller |
| **Total (37)** | 22 / 15 / 0 | 15 / 22 / 0 | **23 / 14 / 0** | |
| Consistency score | 70 | 78 | **85** | |
| Beginner friendliness score | 71 | 76 | **83** | |
| Engine-room leaks on primary paths | — | 26 | **22** (16 of the 26 gone; 12 found/kept) | |

(PASS / PARTIAL / FAIL.) Note: v1.447.0's I was 10/4/0 with I1, I6, I12,
I13, I14 counted as the four PARTIALs plus one (the baseline's own table lists
five PARTIAL rows; its summary line says 4). Read from the rows, the move is
I12, I13, I14 to PASS; I1 and I6 stay PARTIAL.

---

## 1. Per-item tables

Columns: mark · move vs v1.447.0 · evidence (file:line) · what the user meets
today · what is missing · smallest fix (size).

### H. Consistency

| # | Item | Mark | Move | Evidence | Experience | Missing | Smallest fix (size) |
|---|---|---|---|---|---|---|---|
| H1 | Same status terminology | PARTIAL | = (much smaller; code) | `lib/statusWords.ts` is read by 25 files now (12 at v1.447.0); `codingSessions`, `mission`, `kanban`, `threadStatus`, `runningNow`, `BuildCodeView` route through it (Wave B, pin `status-words-one-table-v1449`). Left: `components/agents/mission/ProjectWork.tsx:37-38` tabs "Waiting on you" / "Completed" (the table says "Waiting for you" / "Done"); `AgentInbox.tsx:548` section "Running" (table: "Working"); `lib/mission.ts:364-365` headline "Finished, but part of it failed" (deliberate sentence, fine). | Every badge, row and lane says Done / Working / Waiting for you; a project's tab strip alone says "Waiting on you" and "Completed". | One file's two tab labels. | Read the two tab words from `statusWords` (S). |
| H2 | Same success behaviour | PASS | = | One-notice fixture / `comm/notifier.bell_pings`; `RunResultCard withRow`; Build › Code receipt now leads with ✓/⚠ and the app's duration words (Wave B1). | One verdict per run, every door. | — | — |
| H3 | Same failure presentation | PARTIAL | = (smaller; code) | `LoadFailedLine` in 21 files (14). Raw text left: `app/autonomy/page.tsx:486,578,657,719,934,1230` `<ErrorNote>{x.error.message}</ErrorNote>` (6); `components/settings/ModelTeam.tsx:516`; `components/StandingGrants.tsx:145`; `components/ui.tsx:560-574` `DataError` "Could not load {what}: {error.message}" used by `AgentHistory`, `SafetyChecks`, `BrowserPage`; `app/creative/page.tsx:2524`; 0i's "about 25 press failures" (Fleet, Skills, What agents made, Updates, Browser, Creative) still `setError(String(e))`. | Chat, both Code views, Build terminal chat and 21 panels give "X couldn't load. Try again."; Autonomy, Model team, Standing grants and three DataError users print the server's raw sentence. | Read failures on 5 files; press failures on ~6 pages. | `couldntLoad(...)`/`LoadFailedLine` in the 5 files and in `DataError` itself (one change fixes 3 pages) (S). |
| H4 | Same approval behaviour | **PASS** | ↑ (code) | Add-on `sidepanel.html:771` "Allow once"; `PaneAsk.tsx:24-25,81` shows `ENTER_BLOCKED_NOTE`; chat card, DockAsk, Build › Code `CodeAskCard`, the mission screen (now mounts the chat's `ApprovalCard`, Wave B), the session page and the bell share `lib/approvalWords`. Autonomy's grant offer (`app/autonomy/page.tsx:1380-1400` "Allow exactly this" / "Allow" / "Not now") is an offer of a standing grant, not an ask card; "Decline" stays only on question/coach/library cards (`ElicitationCard.tsx:507`, `AgentCoach.tsx:148`, `LibraryCards.tsx:36`). | Every ask says Allow once / Allow for this chat|run|session / Deny; Enter's refusal is said on every docked card. | — | — |
| H5 | Same connection behaviour | PARTIAL | = (smaller; code) | `CONTROLLED_IN_SECTION` now holds `browser_access`, `backup_mirror_dir`, `backup_mirror_media` (`SettingsHome.tsx:204-225`); `custom_base_url`/`ollama_base_url` stay as plain advanced rows by decision (folded). Still two forms for one thing: Model servers page "Add a node" / "Base URL" / "Add node" (`app/fleet/page.tsx:1062,1073,1165`) vs Connections "Add a model server" / "Server address" / "Save endpoint" (`ConnectionsPage.tsx:1666,1690,1787`). NEW: an MCP server added from chat is a "tool server" (`settings/records_tools.py:110-144`, `lib/appConnect.ts:148,159`) and on the Tools page an "extension". | Adding a model server reads three different ways on two pages; adding an extension from chat calls it a tool server. | One form name; one word for an MCP server. | Fleet's card → "Add a model server" + "Server address"; pick one of extension / tool server (S). |
| H6 | Same progress conventions | PARTIAL | = | Chat, Chat › Code, Build terminal chat share `WorkLine`/`TurnClock`; Build › Code and missions draw their own (by design). `DirectoryTree.tsx:220` still "loading…" lowercase in its non-calm mode, `:392,460` own literals. | Same work line on chat surfaces; two own loading words. | Minor. | `LoaderInline` in `DirectoryTree` (S). |
| H7 | Same expandable-detail conventions | PASS | = | `DETAILS_WORDS` fold, `StepDetailPanel`, `ReplyLine` toggle, Build › Code receipt `<details>` with ✓/⚠ lead. | — | — | — |
| H8 | Buttons behave predictably | PARTIAL | = (smaller; code) | `components/DisabledNote.tsx` + `lib/whyDisabled.ts` now used by chat Send (`app/chat/page.tsx:2948`), Build › Code Start, Directory Connect, the key door, Add a web service. Left: Schedules "Add schedule" `app/schedules/page.tsx:985-991` disabled by `!finalName \|\| !triggerReady \|\| !payloadReady` with no visible reason (the name is now filled from the task, `:509`, so it is rarer); "Add to project knowledge" reason only in label/title (`page.tsx:1775-1811`). | Most greyed primary buttons say why under them; Schedules does not. | One button. | A `DisabledNote` under Add schedule (S). |
| H9 | Navigation is predictable | PASS | = | `lib/surfaces.ts` five rooms; v1.452.0 renamed four pages with the OLD words as aliases (`lib/nav.ts:283-286,318-321,449-452,486-501`); `/fleet` left `CRUMB_LABELS` (title and label agree). | — | (Rename leftovers: M6/M7.) | — |
| H10 | Similar actions look similar | **PASS** | ↑ (code) | Autonomy proposals now `Button` + `ConfirmButton` (`app/autonomy/page.tsx:609-621`), grant offers `Button` (`:1380-1400`); `IconButton`, calm `Badge`/`ConfirmButton`, sentence case. Remaining: the Connections "connect" dropdown is a custom boxed menu; `ErrorNote` boxes (234 uses) vs calm lines on some pages. | One look across pages. | Cosmetic only. | — |
| H11 | Destructive actions consistently handled | **PASS** | ↑ (code) | No native `confirm()` left (`components/PageQuestion.tsx`, `KNOWN_NATIVE_CONFIRMS` empty, Wave D3); Undo in chat, Build terminal chat, DocPreview, Creative ask on the page; Sessions' wipe moved into a "More actions" ⋯ menu (`app/sessions/page.tsx:331-341`). | Delete/Remove two presses; Undo one in-page question everywhere. | — | — |
| H12 | Empty states explain what to do | PASS | = | `<Empty>` with action across data pages. | — | — | — |
| H13 | Loading states explain what is happening | **PASS** | ↑ (code) | `lib/buildStuck.ts:23-39`: "Detecting…" ends in "Couldn't check which coding tools are installed. Try again" (20 s / failed read); "Reading <tool>'s model list…" ends in "Couldn't read <tool>'s model list."; the terminal list's failure is a `LoadFailedLine`; splash second line at 15 s (Wave C). | Every wait has an end. | — | — |
| H14 | Background work does not disappear | **PASS** | ↑ (code) | `lib/runningNow.ts:30-33,63-66` workflow runs (`/workflows/runs?limit=50&slim=true`), media jobs, night tasks; every live Run history row has Stop (Wave B); `reflex.failed` and watcher proposals reach the bell (v1.446.0). | A running workflow, media job or night task is in Running now. | — | — |
| H15 | Reopening reconstructs understandable state | PASS | = | Drafts, `turnCache`, Build restore words, carry-on, interrupted line. | — | — | — |

H: **10 PASS · 5 PARTIAL · 0 FAIL** (v1.447.0: 5 / 10 / 0). Every move is a
code change verified in the tree; none is a looser reading.

### I. Hide the engine room

Primary path = Chat, Build (both views) and Chat › Code, Projects, the
Settings home and its Connections/Models groups, first run, the bell/tray/
phone. Advanced = Model servers page (/fleet), Secrets, Webhooks,
Self-development, the extension Edit door, Help's "another device" section,
folded "More options" rows.

| # | Term | Mark | Move | Evidence | Experience | Missing | Smallest fix (size) |
|---|---|---|---|---|---|---|---|
| I1 | MCP | PARTIAL | = (smaller; code) | Gone: the Session tab heading is "Its own tool servers" (`CodePanes.tsx:815`), reload "Reload its tool servers" (`lib/codePanes.ts:411`). Left on the same primary tab: the daemon's notes under that heading — `src/iron_jarvis/hosted/tool_setup.py:140` "{tool} does not report its own MCP servers here.", `:145` "…which of its own MCP servers started…", `:146` "{tool} reported no MCP servers of its own." (shown by `CodePanes.tsx:816-819` whenever none are listed — the common case for Pi/OpenCode). Also `src/iron_jarvis/daemon/routes/knowledge.py:569` "{name} is not an MCP memory base." (404); dead constant `app/chat/page.tsx:1584` "Extensions (MCP)" (nothing renders it); advanced: `components/memory/baseCatalog.ts:97` "An MCP memory server", `LegacySettingsForm.tsx:573` (only reached with an older daemon). | A heading says "tool servers", the line under it says "MCP servers". | Three daemon sentences. | Reword the three `tool_setup` constants with the heading's word (S). |
| I2 | transport | PASS | = | 0 prose hits. | — | — | — |
| I3 | SSE | PASS (advanced) | = | `EditPack.tsx:204`, `AddPack.tsx:979`, `lib/extensionWords.ts:129` (title). | Behind the Edit door / a tooltip. | — | — |
| I4 | stdio | PASS (advanced) | = | `EditPack.tsx:204`; `TerminalPane.tsx:123` "over stdio" rides a recipe row's `title` only. | — | — | — |
| I5 | inference server | PASS | = | 0 hits. | — | — | — |
| I6 | provider internals | PARTIAL | = (smaller; code) | Gone: Settings home card (`SettingsHome.tsx:98` "Your model plans and sign-ins, API keys, your own model servers, and several accounts."), `default_provider` label "Default AI" (`schema.py:303`), "All providers connected" → "Everything is connected" (`ConnectionsPage.tsx:2440`), "See your plans and model servers" (`:2523`). Left: Usage blurb "Token spend and run volume across your providers." ×3 (`lib/nav.ts:551`, `CommandPalette.tsx:292`, `app/usage/page.tsx:476`); palette "Add a provider account, API key, or local endpoint." (`CommandPalette.tsx:249`); Everything › Status "N provider hiccups in the last 24h" (`components/overview/StatusOverview.tsx:169`); Activity "Switched provider after a failure" (`lib/eventWords.ts:45` + its notifier twin); the chat's live step label "Provider claude-cli failed: …" with the RAW id (`components/chat/stepLabel.ts:151`); TurnReceipt fallback "the provider" (`TurnReceipt.tsx:283`); `SecretPasteNotice.tsx:25` "API key for a model provider"; `lib/ironProxy.ts:295` "provider busy". Raw provider ids in prose: 1 (stepLabel). | The everyday Settings and Connections words are plain; the Usage blurb, the Status line, an Activity headline and one chat step label still say "provider", one with an id. | ~8 strings. | Usage → "What each model used and cost"; "N model failures"; "Switched to another AI after a failure"; `stepLabel` through `providerDisplay` (S). |
| I7 | harness | PASS | = | 0 prose hits; daemon `hosted/words.py:416` "The harness" is a fallback label for an unknown tool (unreachable in practice). | — | — | — |
| I8 | runtime | PASS (advanced) | = | `sandbox_runtime` advanced (folded); `lib/stepDetail.ts:255-256` stripped from rows. | — | — | — |
| I9 | daemon | PASS | = | Only `runPageWords.ts:303` (an OLD stored summary mapped) and `MaintenanceTools.tsx:724` "daemon.log" (a file name); `desktop/main.js` dialogs have none (label ids only). | — | — | — |
| I10 | context implementation details | PASS | = | Fleet "Context window"/"ctx" (`app/fleet/page.tsx:297`), `EnvelopeCard.tsx:541`, folded "Context windows" rows. | — | — | — |
| I11 | Tailscale / network | PASS (advanced) | = | `app/help/page.tsx:1017` in the another-device section; `lan_access` label plain. | — | — | — |
| I12 | raw configuration | **PASS** | ↑ (code) | `SettingsHome.tsx:237-250,490-494,659` `foldedRow` = `advanced` or `type === "dict"`, under a closed "More options (N, M changed)" per group; search and `?focus=`/`#settings-<key>` open it. The 82 unfolded rows are everyday (list in scratch). Still in the advanced fold: "Send coding work to the fleet", "Fleet coding target", "Fleet telemetry" (`schema.py:392-401`) — old page word, see M7. | A CPA owner sees the everyday rows; the 50 expert rows wait behind one fold. | — | — |
| I13 | raw JSON | **PASS** | ↑ (code) | `settings/tools.value_label` (`settings/tools.py:230-267`) sends `old_label`/`new_label`; `lib/configCards.ts:119-133` `showValue(v, label)` prefers it, a dict reads as its keys, never `JSON.stringify`; the card links `?focus=<key>`. Remainder: `default_model` has no options, so a model change reads its raw id ("… → subscription"). JSON boxes only under "More options" and on advanced pages (`RestHookups.tsx:183`, `NodeInspector.tsx:200`, `MaintenanceTools.tsx:738` "Diagnostics JSON" for a report). | "Approvals in chat: Ask before commands → Auto". | `default_model`'s "subscription" id. | `value_label` names "subscription" as "<plan>'s own default" (S). |
| I14 | environment variables | **PASS** | ↑ (code) | Launch menu's "isn't on your PATH" is gone (no `PATH` prose in `components/terminal`, `app/terminals`). Advanced only: `ToolsPage.tsx:2191` manual add form. Rare daemon hint: `src/iron_jarvis/iron_proxy/accounts.py:281` "Install X and put it on PATH, then check the Iron-Proxy card" (CLI_NOT_FOUND from the account manager). | — | One rare hint. | "Install X, then check …" (S). |

I: **12 PASS · 2 PARTIAL · 0 FAIL** (v1.447.0: 10 / 4 / 0).

Relatives the list does not name but a new user meets (detail in section 2):
**endpoint / node** (58 + 26 prose hits; the renamed "Model servers" page
still says "Add a node", "Nodes", "Rename this endpoint"; the model menu and
receipts call the custom slot "your own endpoint"), **Iron-Proxy** in daemon
sentences on the coding surfaces (canon row 29), **tool server** (a NEW second
name for an extension), **reflex** (left over after the v1.452.0 rename),
**Build session** (the old name of a coding session, in Settings labels and
daemon lines), **OAuth app** (Drive/OneDrive/Dropbox one-time setup — now with
boxes, the word stays), **CLI** ("Launch an AI CLI", "No Jarvis capabilities
on this version").

### M. "What does this mean?"

| # | Item | Mark | Move | Evidence | Experience | Missing | Smallest fix (size) |
|---|---|---|---|---|---|---|---|
| M1 | Unexplained technical terminology | PARTIAL | = (smaller; code) | Fixed: "Default provider" → "Default AI"; "Schedule (cron)" → "Repeat" (`GoalContractCard.tsx:254`); PATH gone; the advanced rows folded. Left, non-advanced schema labels (from the 82 unfolded rows): "Autonomy ceiling", "Dry-run mode", "Runs started from inbound messages", "Folder watchers (sentinels)" (`sentinels_enabled`, kept by decision), "Steps per Build session reply", "Learn from Build sessions", "Calendar reminders — …The calendar link is set as a secret." (`schema.py:501`, ease 73), "Approval for one tool — … (allow), (ask), (deny)"; Settings › Models › "What your local models can do" card: "Probe every local endpoint (your added endpoints plus the Ollama/custom slots) with a live tool call and a vision check … routing …", "Probing endpoints…", "No local endpoints found" (`LegacySettingsForm.tsx:838-868`, rendered by `SettingsHome.tsx:80`); `components/GoalsStrip.tsx:254` `on cron "…"` for an unusual schedule; Build Launch menu "No Jarvis capabilities on this version" (`TerminalPane.tsx:150`), "Launch an AI CLI in this terminal" (`:1600`); `documents/ocr.py:86` "OCR is turned off (settings: ocr_enabled = false)" — no such row in `settings/schema.py` (ease 48, open). | The labels a person scans are mostly plain; about ten still need the help line. | ~10 strings. | Plain labels ("How far it may act on its own", "Only suggest, never act", "Work started by your messages", "Steps per coding reply", "Folder watchers"); reword the capabilities card; OCR sentence (S). |
| M2 | Ambiguous labels | PARTIAL | = (smaller; owner decision applied) | v1.452.0: Watchers, Rules, What agents made, Model servers (`lib/nav.ts`). Two "Code" views told apart by tool (v1.420). Left: "Session" has three meanings — Sessions page (`app/sessions/page.tsx:303-304` "Run agents and inspect past sessions.", nav blurb "Every task an agent ran, step by step."), a coding session (Chat › Code), a "Build session" (Settings rows, daemon notes); "Session board", "Secrets", "Self-development" unchanged (the owner renamed only four). | A first-time user still cannot tell "Sessions" from "Coding sessions". | One page name. | Rename the Sessions page "Agent runs" (owner's call) and say "coding session" in the two Settings rows (S). |
| M3 | Ambiguous icons | **PASS** | ↑ (code) | `ThreadGroups.tsx:470-477` a visible "Needs you" word on a waiting row (`thread-needs-you-<id>`) plus sr-only words (`:383,477`); running = pulse (a second cue); `IconButton` names every icon button. | The chat list says "Needs you" in words. | — | — |
| M4 | Statuses without consequences | PARTIAL | = | `components/MoodOrb.tsx:67-68` still two amber lines: "Waiting for you — a reply needs your answer" / "Attention needed — something is waiting for you". | Two amber states read alike. | One line. | Name the thing in `alert` (S). |
| M5 | Controls whose effects aren't obvious | PARTIAL | = (smaller; code) | Fixed: Chat › Code's switch "Work in a separate copy of the folder", OFF, with what on/off do (`lib/buildGit.ts:42-50`) + "Keep these changes"; "Every minute" last with a warning (`lib/schedules.ts:21-30`); Rules form opens on "a message arrives → an agent does a task" (`app/reflex/page.tsx:312,376-389`); Night shift has the folder picker and "Make this folder a repository" (`TonightList.tsx:82,131`); visible disabled notes (H8). Left: Try on's chip still explains in git words with no press ("Try on needs a folder in a git repository: each model works on its own branch", `HarnessStartComposer.tsx:167-176`; ease 33, partly); the Rules form's action "Start a session" (`app/reflex/page.tsx:245,808`) and subtitle "run a workflow, a remote agent, or a session" (`:652`); Autonomy's "Allow exactly this" (`app/autonomy/page.tsx:1389`); Schedules' Add (H8). | Most switches say what they change; Try on and "Start a session" still assume the engine's words. | 3 strings + 1 press. | "Run an agent" in the Rules form; on Try on offer the Night shift's "Make this folder a repository" press (S). |
| M6 | Duplicate concepts | PARTIAL | = (smaller; owner decision applied) | Watcher now one name on screen (page, tool words `lib/builtinToolWords.ts:199-201,370-372`, OriginChip "Watcher", loopWords) except the setting label "Folder watchers (sentinels)". Rule: page "Rules", tool words "a rule that acts on its own", OriginChip "Rule", bell's failure row "A rule couldn't start" — but a run started BY a rule is "Your reflex X" / "A reflex rule" in the bell (`NotificationBell.tsx:358`), the tray (`desktop/main.js:4181`) and the phone (`comm/notifier.py:248`); Webhooks page "add a reflex →", "(a reflex decides what to do about it)" (`app/webhooks/page.tsx:355,378`); Saved workflows "a schedule or reflex rule" (`SavedWorkflows.tsx:265`); the Rules page keeps a separate "Triggers" card (`app/reflex/page.tsx:1166`). Compact (Build › Code, Claude Code's own word, `ContextBreakdown.tsx:237`) vs Summarize (Chat › Code) unchanged (deliberate). NEW: extension (Tools page, canon row 22) vs tool server (chat's `mcp_server_add` card, `lib/appConnect.ts:148,159`, run words `lib/builtinToolWords.ts:124,314`, `lib/runWords.ts:119`, Build › Code `CodePanes.tsx:815`). | A rule is "Rule" on its page and "reflex" in the notice the user gets when it runs; an MCP server is an "extension" on Tools and a "tool server" in chat. | The reflex leftovers; one word for an MCP server. | "Your rule X" / "A rule" in the three twins; two Webhooks lines; one SavedWorkflows line; decide extension vs tool server and add the loser to VOCABULARY.md + the scanner (S). |
| M7 | Multiple names for one concept | PARTIAL | = (smaller; code) | The user's model server: "Model servers" (nav, page title `app/fleet/page.tsx:1227`, Settings section), "Model servers on your network" (`lib/modelServers.ts:11`), "Your model servers" (Connections list, ConnectDoors `:277`), but "your own endpoint" (`lib/onboarding.ts:236`, `providerDisplay("custom")` — the model menu, receipts, PreflightNote), "Custom endpoint"/"Ollama endpoint" (seed labels `lib/onboarding.ts:383-384`), "Connected · your endpoint · no key needed" (`lib/modelServers.ts:14`), "Save endpoint" / "Endpoint saved. Pick … in any model picker." / aria "Endpoint name" (`ConnectionsPage.tsx:1787,1003,1490`), "Your accounts and model endpoints." (`lib/nav.ts:481`), "Connections → Your endpoints" (`CommandPalette.tsx:154-155`), and on the renamed page itself "Add a node", "Nodes", "Rename this endpoint", "Remove this endpoint (also clears its Settings URL, retiring the provider)" (`app/fleet/page.tsx:1062,1305,569-582`). The account manager: dashboard lines on coding surfaces are gone, but the DAEMON's words shown there still say Iron-Proxy (section 2, items 2-4). The coding session: "coding session" (Chat › Code, Build › Code lists) vs "Build session" (Settings `build_round_budget`, `build_reflect`; `harness/build_accounts.py:349` "…to run Build sessions on your accounts"). MCP server: extension vs tool server (M6). Status words: H1 (one file). | One server has about five names, down from seven; the page renamed "Model servers" still calls each one a node. | ~15 strings. | "model server" for every `endpoint`/`node` string a person reads; `providerDisplay("custom")` → "your model server"; "coding session" in the two rows and the daemon note (S). |
| M8 | Architecture exposed unnecessarily | PARTIAL | = (smaller; code) | Fixed: Settings home card, Activity headlines (all 105 event types worded, `lib/eventWords.ts`, pinned both ways), 50 rows folded, Drive/OneDrive/Dropbox App ID + App secret boxes (`components/connections/OAuthAppForm.tsx`, no hand-typed vault names). Left: Model servers page (nodes, "Force a fresh probe of every node" `app/fleet/page.tsx:1255`, "Base URL", "ctx"); the capabilities card in Settings › Models (M1); Build › Code Session tab's "MCP servers" notes (I1); the Drive/Dropbox setup still says "needs an OAuth app that you register yourself" / "Manage OAuth app in …" (`ConnectionsPage.tsx:1193,1217`); remote agent "http-task (task API)" / "openai-chat (chat/completions)" (advanced). | The engine shows through on the Model servers page, one Settings › Models card and one Build › Code tab. | 4 places. | Reword the Fleet page's node words; the capabilities card; "a sign-in app of your own" for OAuth app (S). |

M: **1 PASS · 7 PARTIAL · 0 FAIL** (v1.447.0: 0 / 8 / 0). Every PARTIAL is
smaller than at v1.447.0; none is a FAIL.

---

## 2. Engine-room leak list (primary paths first)

Format: where · what the user reads · plain replacement.

### On primary paths (22)

1. `src/iron_jarvis/hosted/tool_setup.py:140,145,146` (Build › Code › Session tab, under the heading "Its own tool servers", `CodePanes.tsx:815-819`) · "{tool} does not report its own MCP servers here." / "…which of its own MCP servers started…" / "{tool} reported no MCP servers of its own." · "…its own tool servers…" (whatever word M6 settles on).
2. `src/iron_jarvis/harness/build_accounts.py:349-351,435-436` (Chat › Code account menu, `components/harness/HarnessAccounts.tsx:226-231`; 409 details in `routes/build.py:632,4454`) · "Iron-Proxy is off; turn it on in Connections to run Build sessions on your accounts." / "Iron-Proxy has not listed its accounts yet…" / "Iron-Proxy needs an update…" · "Several accounts is off; turn it on in Settings › Connections to run coding sessions on your accounts." / "Your accounts are not listed yet…" / "The account manager needs an update…" (canon row 29).
3. `src/iron_jarvis/terminals/pane_accounts.py:104-111,233,286-304,324,446-451,679-683` (Build terminal account chip and its refusals) · "Iron-Proxy is off; turn it on…", "…no longer exists in Iron-Proxy", "…turned off on the Iron-Proxy card", "Iron-Proxy did not answer in time…" · "Several accounts is off…", "…no longer in your accounts", "…turned off on the Several accounts card".
4. `src/iron_jarvis/terminals/continue_on.py:69-74,573,637` and `src/iron_jarvis/hosted/words.py:330` (Continue on the next account; Build › Code) · "open a pane on an Iron-Proxy account", "There is no other Claude account in Iron-Proxy…", "Iron-Proxy did not mark …" · "on one of your accounts", "There is no other Claude account in your accounts…" (also "pane" → "terminal").
5. `dashboard/components/NotificationBell.tsx:358`, `desktop/main.js:4181`, `src/iron_jarvis/comm/notifier.py:248` (bell row, tray toast and phone message for a run a rule started) · "Your reflex X" / "A reflex rule" · "Your rule X" / "A rule" (v1.452.0 rename leftover; three twins).
6. `dashboard/components/chat/stepLabel.ts:151` (the chat's live step label on `provider.failed`) · "Provider claude-cli failed: …" (raw id) · "<providerDisplay(p)> failed: …".
7. `dashboard/lib/onboarding.ts:236` (`providerDisplay("custom")` → model menu group, TurnReceipt, PreflightNote, failure lines) · "your own endpoint" · "your model server".
8. `dashboard/components/chat/PreflightNote.tsx:80-81` (above the composer) · "…check the endpoint." / "…bring the endpoint back." · "…check that model server." / "…start that model server again."
9. `dashboard/lib/modelServers.ts:14` (Connections' model-server card pill) · "Connected · your endpoint · no key needed" · "Connected · your model server · no key needed".
10. `dashboard/components/settings/pages/ConnectionsPage.tsx:1003,1490,1787,958` · "Endpoint saved. Pick … in any model picker.", aria "Endpoint name", "Save endpoint", "API key for endpoint" · "Model server saved…", "Server name", "Save model server", "API key for this server".
11. `dashboard/lib/nav.ts:481` (Connections blurb in the palette / Everything) · "Your accounts and model endpoints. Connect once and everything can use them." · "Your accounts and model servers. …".
12. `dashboard/components/CommandPalette.tsx:154-155,249` · "Connections → Your endpoints", "Rename, re-point or remove a model endpoint you added.", "Add a provider account, API key, or local endpoint." · "Connections → Your model servers", "…a model server you added.", "Add an account, an API key or your own model server."
13. `dashboard/lib/nav.ts:551`, `dashboard/components/CommandPalette.tsx:292`, `dashboard/app/usage/page.tsx:476` · "Token spend and run volume across your providers." · "What each model used and cost."
14. `dashboard/components/overview/StatusOverview.tsx:169` (Everything › Status line) · "N provider hiccups in the last 24h" · "N model failures in the last 24h".
15. `dashboard/lib/eventWords.ts:45` + `comm/notifier.EVENT_WORDS` twin (Activity) · "Switched provider after a failure" · "Switched to another AI after a failure".
16. `src/iron_jarvis/settings/schema.py` unfolded rows · "Steps per Build session reply", "Learn from Build sessions", "Folder watchers (sentinels)", "Autonomy ceiling", "Dry-run mode", "Runs started from inbound messages", "…The calendar link is set as a secret." (`:501`) · "Steps per coding reply", "Learn from coding sessions", "Folder watchers", "How far it may act on its own", "Only suggest, never act", "Work started by your messages", "Add your calendar link on the Rules page".
17. `dashboard/components/settings/pages/LegacySettingsForm.tsx:838-868` (Settings › Models › "What your local models can do", via `SettingsHome.tsx:80`) · "Probe every local endpoint (your added endpoints plus the Ollama/custom slots) … routing …", "Probing endpoints… (a sleeping box can take a minute)", "No local endpoints found" · "Check what each of your own models can do (a test with a tool and a picture)…", "Checking your models…", "No models of your own yet".
18. NEW (Wave A) `src/iron_jarvis/settings/records_tools.py:110-144`, `dashboard/lib/appConnect.ts:148,159`, `lib/builtinToolWords.ts:124,314`, `lib/runWords.ts:119`, `lib/toolWords.ts:92`, `agents/mission.py:367,504` · "Tool server “x” added", "add a tool server" · "Extension “x” added", "add an extension" (canon row 22) — or change the canon to "tool server" everywhere (Tools page included).
19. `src/iron_jarvis/documents/ocr.py:86` (a file note relayed in chat) · "OCR is turned off (settings: ocr_enabled = false)" · "Reading scanned pages is turned off" — and there is still no `ocr_enabled` row in `settings/schema.py` (ease 48).
20. `dashboard/components/terminal/TerminalPane.tsx:150,167,1600,2012` (Build's Launch menu) · "No Jarvis capabilities on this version", title "Jarvis capabilities over stdio", "Launch an AI CLI in this terminal", "…another terminal's AI CLI" · "Starts without Iron Jarvis's tools on this version", "Iron Jarvis's tools ready", "Start a coding tool in this terminal".
21. `dashboard/components/CommandPalette.tsx:145` and `dashboard/app/documents/page.tsx:1157` · "Documents → Redact PII", "Scan for PII" (the card title itself is now "Hide personal details", `:1098`) · "Documents → Hide personal details", "Find personal details".
22. `dashboard/app/schedules/page.tsx:901` · "Add more on the Notifications page" as plain text (ease 73) · a link to Settings › Notifications.

### Advanced or rare (acceptable, listed for completeness)

- Model servers page (`app/fleet/page.tsx:130,372,403,534,569-582,729,926,1040,1062,1073,1104,1165,1255,1305,1311`): "endpoint", "node", "Add a node", "Nodes", "Base URL", "Force a fresh probe of every node", "retiring the provider" — now a rename inconsistency as much as a leak (the page is titled "Model servers").
- Folded schema rows: "Send coding work to the fleet", "Fleet coding target", "Fleet telemetry", "Sample local endpoints … for the Model servers page" (`schema.py:392-401`), "Custom endpoint URL", "Voice: speech-to-text endpoint".
- Daemon 404 details a page may show: `routes/autonomy.py:199,205` "sentinel not found"; `routes/reflex.py:69,82,108` "no such reflex rule"; `routes/fleet.py:633,1264` "isn't a routable fleet node"; `routes/knowledge.py:569` "{name} is not an MCP memory base."; `routes/build.py:3036…4152` "no such Build session"; `routes/codelab.py:63` "no such code artifact".
- Webhooks page `app/webhooks/page.tsx:355,378` "add a reflex →", "(a reflex decides what to do about it)"; `components/workflow/SavedWorkflows.tsx:265` "a schedule or reflex rule".
- Drive/OneDrive/Dropbox one-time setup `ConnectionsPage.tsx:1162,1193,1217,1287,1364`: "OAuth app", "OAuth was cancelled or failed", "OAuth 2.0" (the vault names are gone).
- `src/iron_jarvis/iron_proxy/accounts.py:281` "…put it on PATH…" (CLI_NOT_FOUND hint, rare). Account refusals naming Iron-Proxy in its own errors (`accounts.py:437-508`) are allowed by the canon.
- `lib/profileShare.ts:101` (Memory › Share with Build) "…in N Iron-Proxy account folders" — not a coding surface by the scanner's scope.
- `components/tools/EditPack.tsx:204`, `AddPack.tsx:979`, `ToolsPage.tsx:2191`: stdio / Streamable HTTP / SSE / "environment variables" (Edit door, manual add).
- `components/agents/SetupCard.tsx:1964-1965,2274,2484`: "Base URL", "POST JSON to", http-task / openai-chat (remote agent).
- `components/connections/RestHookups.tsx:183-186` "Config (JSON)", `components/workflow/NodeInspector.tsx:200` "Arguments (JSON)", `MaintenanceTools.tsx:699,738` "Diagnostics JSON".
- `components/memory/baseCatalog.ts:97-107`, `LongTerm.tsx:414,1216,1225`: "An MCP memory server", "Endpoint URL", "OAuth connection".
- `app/help/page.tsx:1017` Tailscale (another-device section); `app/help/page.tsx:145` "an API key or OAuth".
- `app/creative/page.tsx:2765-2766,3293,4011`: "the CLI", "No AI CLIs detected".
- `app/self-dev/page.tsx:83,158`: "worktree" (allow-listed on purpose).
- `app/schedules/page.tsx:201,942,972,1785` "Custom cron", "Advanced cron…", "Cron expression" (behind Advanced); `components/GoalsStrip.tsx:254` `on cron "…"` for a schedule no preset describes.
- `app/chat/page.tsx:3641` (title on a pre-v1.165.0 message) "Verify the endpoint's tool support".
- `app/chat/page.tsx:1584` dead constant "Extensions (MCP)" (nothing renders it).
- `docs/HANDBOOK.md` release-history sections still name Reflexes, Sentinels, the Fleet page (`:1744,2667,3564,4895,8815`) — history, but the Guide reads it.

### Gone since v1.447.0 (verified 0 prose hits or replaced)

1. Settings home Connections card "Model providers, sign-ins, API keys, endpoints and Iron-Proxy accounts." → "Your model plans and sign-ins, API keys, your own model servers, and several accounts." (`SettingsHome.tsx:98`).
2. `default_provider` "Default provider" → "Default AI" (`schema.py:303`; alias kept).
3. Build › Code "Its own MCP servers" heading → "Its own tool servers" (`CodePanes.tsx:815`) — but see leak 1.
4. "Reload MCP servers" / "reads its MCP settings again" → "Reload its tool servers" / "…tool-server settings…" (`lib/codePanes.ts:411-413`).
5. `lib/buildApp.ts` "Iron-Proxy has these marked as at their limit." — gone.
6. `ModelMenu.tsx` "Iron-Proxy picks the first free one" — gone (only comments).
7. `lib/chatAccount.ts`, `lib/paneAccounts.ts`, `ModelTeam.tsx` Iron-Proxy lines — gone (scoped scanner row `RETIRED_ON_SURFACES`, Wave D).
8. Chat "Setting changed" wire values ("approve_for_me → auto", "mark1 → daylight", JSON dicts) — replaced by `old_label`/`new_label`.
9. ConnectDoors "Your model fleet" → "Your model servers" (`ConnectDoors.tsx:277`).
10. Nav "Local fleet" / crumb "Fleet" → "Model servers" (v1.452.0).
11. "See subscription &amp; local providers" → "See your plans and model servers" (scanner now decodes entities, `retiredWords.ts:154-182`).
12. "All providers connected 🎉" → "Everything is connected 🎉".
13. "Its provider disappears…", "Each endpoint is its own provider in every model picker.", "Add an endpoint", "Endpoint base URL", "Saved endpoints" (heading) → "Add a model server", "Server address", "Your model servers".
14. Gmail/Drive/OneDrive `<provider>_oauth_client_id` typed into Secrets → App ID / App secret boxes (`OAuthAppForm.tsx`).
15. Goal card "Schedule (cron)", "a cron fires on schedule" → "Repeat".
16. Activity raw event names (67 of 87) → every one of 105 published types worded.
17. Chat-list colour-only "needs you" dot → visible "Needs you".
18. Launch menu "isn't on your PATH — get it" — gone.
19. Slack form "Enable two-way (true/false)", "Set to true…" → switches "Listen for incoming messages" (`daemon/app.py:3554,3577,3605`).
20. "…make the identical call again. Pending approval id=appr_…" (browser and computer-use risk door) → a once-only card that resumes (`core/risk_door.py`, Wave B4).
21. Desktop "The window stopped responding (oom)… live in the local service." → "The window ran out of memory and was reloaded. Nothing was lost." (`desktop/main.js:6372-6373`).
22. "Install … the Grok CLI" on the empty state — now reads the product names (the Rescan line still says "Grok CLI", which is that product's display name in `lib/subscriptionClis.ts`).
23. Ease 73 one-liners: Self-development's "Settings → Advanced", Autonomy's "trust surface for the Motivation Layer" (now "Goals Jarvis works toward on its own, and the switch that stops it all."), Watchers "always-on", the Sessions wipe as main button, TitleBar "Overview", Help's "Settings → Maintenance" (now a link), Documents card title "Redact PII", Build › Code "Iron-Proxy" and "MCP" lines, `sessionRail` "Claude Code's tool names", HomeReadiness "Ask anything below" (now "Type your question in the box"), `chatControlWords` "tool slots" (now "You can turn on up to N tools per chat."), Launch "isn't on your PATH".

Still there from the v1.447.0 list: items 13-14 in part (Connections' "Save endpoint" and the nav/palette blurbs), 17 → fixed, 21 (provider words), 23 (OCR), 25 ("MCP memory base" 404), plus the daemon Iron-Proxy lines (0i "left open").

---

## 3. Category scores

**Consistency: 85** (v1.447.0: 78). What moved it, all verified in code:
the one status table is now read by 25 files and pinned by a scan; one set of
approval words on every asking surface including the browser add-on and a Build
terminal's ask (with the Enter note); no native `confirm()` left anywhere;
Running now lists workflow runs, media jobs and night tasks and every live
Run history row has Stop; every wait in Build has an end; Autonomy uses the
shared buttons; the Sessions wipe moved into ⋯. What holds it: a NEW second name
for an extension ("tool server", Wave A) that also leaks into the Build › Code
Session tab while the Tools page says "extension"; the v1.452.0 page rename
left "reflex" in three notification twins (bell, tray, phone) and two Webhooks
lines, and the Model servers page still calls its rows nodes and endpoints;
"Build session" beside "coding session"; raw error text on Autonomy, Model team,
Standing grants and three DataError pages; two model-server forms.

**Beginner friendliness: 83** (v1.447.0: 76). What moved it: the Settings page
folds the 50 expert rows and the JSON boxes; the Settings home card and the
first Models row speak plainly ("Default AI"); the chat's Setting-changed card
shows option names; every Activity headline is a sentence; Drive/OneDrive/
Dropbox setup has two boxes; Chat › Code works straight in the folder by
default with "Keep these changes"; the Rules form, Schedules and the Night
shift open on the everyday choice; the four page names are plain; PATH, cron,
"Iron-Proxy" on the dashboard's coding lines and "MCP" in Build › Code's
headings are gone. What holds it, in order of how often a new user meets it:
the custom model server is "your own endpoint" in the model menu, receipts and
the preflight line, and "endpoint/node" across Connections and the Model servers
page; the daemon's own account notes on the coding screens still say
"Iron-Proxy" (and "Build sessions"); a run a rule started rings as "Your reflex";
the Build › Code Session tab's note says "MCP servers"; "provider" in the Usage
blurb, the Status line, an Activity headline and a chat step label with a raw
id; ten Settings labels (Autonomy ceiling, Dry-run mode, …); the capabilities
card under Models; the OCR sentence naming a setting that does not exist.

---

## 4. Remaining work (impact × frequency × confusion, highest first)

| # | Work | Smallest fix | Size | Where |
|---|---|---|---|---|
| 1 | The custom model server is "your own endpoint" / "endpoint" on the model menu, receipts, preflight line, Connections card and buttons, nav and palette blurbs; the renamed "Model servers" page still says Add a node / Nodes / Rename this endpoint (M7, H5) | `providerDisplay("custom")` → "your model server"; replace every `endpoint`/`node` string a person reads with "model server" (≈20 strings); Fleet's card "Add a model server" / "Server address" | S | section 2 items 7-12; `app/fleet/page.tsx` |
| 2 | Daemon account notes on the coding screens say "Iron-Proxy" (and "Build sessions") — Chat › Code account menu, Build terminal chips, Continue on the next account (canon row 29; 0i "left open") | Reword the ~25 constants in `harness/build_accounts.py`, `terminals/pane_accounts.py`, `terminals/continue_on.py`, `hosted/words.py:330`; extend the daemon-answer scan with the scoped Iron-Proxy row | S/M | section 2 items 2-4 |
| 3 | Two names for an MCP server: "extension" (Tools, canon) vs "tool server" (chat's Wave A door, run words, Build › Code tab) — and the Build › Code note under "tool servers" still says "MCP servers" (I1, M6) | Decide one word; update VOCABULARY.md, `records_tools.py`, `lib/appConnect.ts`, word tables, `tool_setup.py:140-146`; add the loser to the scanner | S | section 2 items 1, 18 |
| 4 | "reflex" after the rename: "Your reflex X" / "A reflex rule" in the bell, tray and phone; Webhooks "add a reflex", "(a reflex decides…)"; SavedWorkflows; 404s (M6) | Rule words in the three twins (they are pinned equal by the one-notice fixture, so change all three together) and three dashboard lines; a scanner row for `reflex` with the data-value exceptions | S | `NotificationBell.tsx:358`, `desktop/main.js:4181`, `comm/notifier.py:248`, `app/webhooks/page.tsx:355,378`, `SavedWorkflows.tsx:265` |
| 5 | Raw server text on read failures: Autonomy ×6, Model team, Standing grants, `DataError` (3 pages); ~25 press failures (H3) | `couldntLoad`/`LoadFailedLine`; change `DataError` itself | S | as listed in H3 |
| 6 | "provider" words: Usage blurb ×3, "provider hiccups", "Switched provider", stepLabel "Provider claude-cli failed" (raw id) (I6) | Four rewords; `stepLabel` through `providerDisplay` | S | section 2 items 6, 13-15 |
| 7 | Ten Settings labels a person scans need their help line; the capabilities card under Models is in endpoint/probe/routing words (M1, M8) | Plain labels; reword `LocalCapabilitiesCard` | S | `schema.py`, `LegacySettingsForm.tsx:838-868` |
| 8 | "Session" means three things; "Build session" names a coding session in Settings and daemon lines (M2, M7) | "Steps per coding reply", "Learn from coding sessions"; owner's call on renaming the Sessions page "Agent runs" | S (code) / owner | `schema.py`, `app/sessions/page.tsx:303` |
| 9 | OCR sentence names `settings: ocr_enabled = false`, a row that does not exist (ease 48) | Add an `ocr_enabled` schema row, or say "Reading scanned pages is turned off" | S | `documents/ocr.py:86`, `settings/schema.py` |
| 10 | Try on's chip explains in git words with no way out (ease 33, partly) | Offer the Night shift's "Make this folder a repository" press | S | `HarnessStartComposer.tsx:151-176` |
| 11 | Schedules "Add schedule" greys out with no visible reason; "Notifications page" is not a link (H8, ease 26/73) | `DisabledNote`; a link to Settings › Notifications | S | `app/schedules/page.tsx:901,985-991` |
| 12 | Orb's two amber states read alike (M4) | Name the thing in `alert` | S | `MoodOrb.tsx:67-68` |
| 13 | Launch menu "No Jarvis capabilities on this version", "Launch an AI CLI" (M1) | Plain words | S | `TerminalPane.tsx:150,167,1600,2012` |
| 14 | Palette "Documents → Redact PII", "Scan for PII"; calendar "set as a secret" (ease 73 leftovers) | Three strings | S | `CommandPalette.tsx:145`, `documents/page.tsx:1157`, `schema.py:501` |
| 15 | Rules form "Start a session"; subtitle "…or a session" (M5) | "Run an agent" | S | `app/reflex/page.tsx:245,652,808` |
| 16 | Project tabs "Waiting on you" / "Completed" (H1) | Read `statusWords` | S | `ProjectWork.tsx:37-38` |
| 17 | Folded schema rows still say "fleet" ("Send coding work to the fleet", "Fleet telemetry") (M7, advanced) | "model servers" | S | `schema.py:392-401` |
| 18 | `default_model` change card shows "subscription" (I13 remainder) | `value_label` names the plan's default | S | `settings/tools.py:230-267` |

Ease-of-use items in my sections, verified against v1.452.0:

| Item | State | Evidence |
|---|---|---|
| 11 two Code views | done | cross-links under both start boxes (v1.445) |
| 12 branch switch | done | `lib/buildGit.ts:42-50`, OFF by default, "Keep these changes" (v1.452.0) |
| 15 Settings expert rows | done | `SettingsHome.foldedRow` |
| 18 Ctrl K Settings | done | `lib/settingsOpen` |
| 26 schedule creation | partly | "Every minute" last + warning (`lib/schedules.ts:21-30`), name filled from the task (`schedules/page.tsx:509`); Add still greys with no visible reason |
| 27 edit rules/watchers | done | PATCH routes + Edit on both pages (Wave D2) |
| 28 rule source off | done | `lib/reflexRules` "cannot hear" line |
| 29 Rules form defaults | done | `app/reflex/page.tsx:312,376-389` |
| 32 Night shift folder | done | `TonightList.tsx:82,131` picker + "Make this folder a repository" |
| 33 Try on where it can't work | partly | the chip says why (`tryOnAvailability`) but in git words and with no press |
| 36 Setting-changed values | done | `old_label`/`new_label` (one remainder: `default_model`) |
| 39 colour-only chat dots | done | `ThreadGroups.tsx:470-477` |
| 40 two Project controls | done | the Project picker has "New project / manage all ↗" (`app/chat/page.tsx:12417`) |
| 41 chat list fails to load | done | `lib/chatList` `loadFailed` + LoadFailedLine (Wave B3) |
| 42 code-block Copy on touch | done | Wave D3 |
| 44 greyed buttons explain only on hover | partly | `DisabledNote` on 5 primary buttons; Schedules' Add and a few others still title-only |
| 45 long pasted message | done | `lib/composerFold.ts` |
| 48 OCR has no setting | **open** | `documents/ocr.py:86`; no `ocr` row in `settings/schema.py` |
| 53 Slack (true/false) | done | `daemon/app.py:3554,3577,3605` |
| 54 one setting, two controls | done (by decision) | `CONTROLLED_IN_SECTION` gained 3 keys; the two endpoint address rows stay plain (folded) because no card lets you type them |
| 56 "Needs Node.js" link | done | `lib/directoryLinks.ts:26-70` "Get Node.js (free)" |
| 59 "5 of 6 helpers" | done | required helpers only (Wave C) |
| 68 desktop raw text | done | `desktop/main.js:6372-6382`, startup dialog plain (Wave D3) |
| 70 Activity raw names | done | `lib/eventWords.ts`, all 105 types |
| 73 one-line wordings (17 rows) | partly: 13 done, 4 open | open: `schedules/page.tsx:901` "Notifications page" not a link; `schema.py:501` "set as a secret"; Documents "Redact PII" still in the palette and "Scan for PII" button; `TerminalPane.tsx:150` "No Jarvis capabilities" (the `:138` half done). `chat/page.tsx` "server-owned"/"grounded" are comments only now |
| 74 Build stuck states | done (4 of 6 checked) | `lib/buildStuck.ts`; Running now workflows; not re-checked: StartComposer menus in a scroll box, Telegram "Find my chat ID" timer |

---

## 5. Regressions

**Fixes undone:** none found. Every v1.447.0 PASS in H, I and M still holds in
the code (H2, H7, H9, H12, H15; I2-I5, I7-I11).

**New inconsistency introduced since v1.447.0 (code, not a stricter reading):**
1. **"tool server"** (Wave A, v1.448.0: `settings/records_tools.py`, `lib/appConnect.ts:148,159`, run/card word tables) — a second user-facing name for what VOCABULARY.md row 22 calls an **extension**. Wave D then renamed Build › Code's "MCP servers" to "tool servers" (`CodePanes.tsx:815`, `lib/codePanes.ts:411`), so the same thing is "extension" on Settings › Tools and "tool server" in chat and Build › Code. The canon was not updated and the scanner has no row, so nothing stops it spreading.
2. **The v1.452.0 rename left old words behind** where a person still reads them: "Your reflex X" / "A reflex rule" in the bell, the tray and the phone (three twins pinned equal to each other, so the rename missed all three together); "add a reflex →" and "(a reflex decides…)" on Webhooks; "a schedule or reflex rule" in Saved workflows; the Model servers page's own "Add a node" / "Nodes" / "Rename this endpoint"; the folded "…to the fleet" / "Fleet telemetry" rows. The CLAUDE.md note says the retired words are deliberately NOT in the scanner (lowercase `reflex`/`sentinel` are data values), which is why these survived.
3. Not a regression but newly visible: the Build › Code Session tab's heading and the daemon's note under it now disagree ("tool servers" / "MCP servers").

---

## 6. Never proven live

- Nothing in this lane was driven in the running or packaged app; every mark is from source. Not seen on screen: the Settings "More options" fold, the chat's Setting-changed card labels, the bell/tray/phone "Your reflex" lines, the Chat › Code account menu's Iron-Proxy note, the Build › Code Session tab's "MCP servers" note, the Model servers page, the Launch menu's words.
- The prose scan reads source literals; a word that reaches the screen through a variable (a schema `label` from the daemon, a daemon `detail`, a tool's own words, a model's text) is not in it. Daemon sentences were read by grep of string literals only; the dashboard-side scoped "Iron-Proxy" scanner row does not cover daemon sentences shown on coding screens.
- The browser add-on's text was read in `sidepanel.html` source, not a built bundle.
- Ease 74's StartComposer-menu placement and Telegram timer were not re-checked.
- Whether `default_model`'s change card really shows "subscription" was inferred from `value_label` (no options, not `default_provider`), not seen.

---

# Lane L — "Did it work?" (14 workflows + chat-first configuration), v1.452.0 (80ec5e89), 2026-11-08

Read-only. Every mark is from the CURRENT code (file:line), re-marked from scratch against the v1.447.0 baseline's bar (`docs/PRODUCT-READINESS-LANES-v1447.md`, lane L). Judged as a first-time, folder-first user who does not use Projects. Nothing was run; the running app was not touched.

## Headline

| | v1.447.0 | v1.452.0 | Move |
|---|---|---|---|
| 14 checklist workflows | 10 PASS / 4 PARTIAL / 0 FAIL | **13 PASS / 1 PARTIAL / 0 FAIL** | +3 PASS |
| Chat-first configuration (6) | 5 PASS / 1 PARTIAL | **6 PASS / 0 PARTIAL** | +1 PASS |

What moved (all CODE changes): Chat › Code coding task PARTIAL → PASS (v1.452.0: separate copy OFF by default, plain words, "Keep these changes"); browser task from chat PARTIAL → PASS and computer-use task PARTIAL → PASS (v1.449.0, G54 closed: `core/risk_door.py`, the stream lane's own risk card); mission PARTIAL → PASS (receipt duration + actions, the chat's ApprovalCard); config-change card PARTIAL → PASS (v1.448.0 display labels).
What moved the other way (a STRICTER READING, not a regression): remote-machine task PASS → PARTIAL — a report from a remote that an agent run handed work to (outside a mission and outside an @-round) lands in a room nothing on screen shows.

---

## 1. Per kind of work: where the user learns it finished, worked, changed, undo/retry

| Kind of work | Finished? (where) | Worked? (verdict words) | What changed | Undo / retry | Verdict |
|---|---|---|---|---|---|
| **Chat reply** | Reply settles; screen readers hear "Reply ready." / "Reply stopped." from an always-mounted `role=status` (`app/chat/page.tsx:4241-4307`, `:13318`); a long reply reaches the tray (`chat.turn_finished`). | "Worked for … · read 2 files · 1 failed" with ✓ / ⚠ (`components/chat/WorkLine.tsx:1037-1051`); receipt "answered by <model> · cloud" (`TurnReceipt.tsx`); failure line "title. fix" + Details. | "N files changed +a −r" under the reply (`ReplyChanges.tsx:29-39`, `ChangedFiles.tsx`); honesty notes for an unbacked save / change / setting claim (`daemon/change_claims.py`). | Undo per file in `ChangedFiles`; Retry / Sign in / "Choose another model…" on the failure line; small failures in a dismissible `chat-notice` (`page.tsx:13407-13419`). | Complete |
| **Chat-started agent run** | Hand-off row settles (`AgentRunRow.tsx:170`, `:437`); RunResultCard `withRow`; bell quiet row / attention if it failed. | "worked for 2 min 5 s · 6 steps · part failed", verdict outside the truncating span; "The run failed: <plain reason>". | AgentRunReceipt: action lines, "in <folder> · Open folder". | Try again / Revert / Keep going / Stop (`RunResultCard.tsx:13-26`, `:130`). | Complete |
| **Chat › Code reply** | ONE reply line (`HarnessMessageRow.tsx:129-144`); live rows from the first frame; finish notice > 60 s. | "Worked for … · 7 steps · Checked: tests passed", warn for failed / cut off; TurnReceipt inline. | Work lands IN the chosen folder by default now (`HarnessStartComposer.tsx:262` `seeded?.branch ?? false`); with the copy ON, "Keep these changes" commits + merges (`HarnessBranch.tsx:252-280`, `:302-309`; words `lib/buildGit.ts:42-54`). Changes pane per reply. | Rewind to here; per-file Undo; Continue from where it stopped / Retry / Sign in / Details (`HarnessFailure.tsx:9-24`); Local only ways out. | Complete |
| **Build › Code reply** | `done` item (`CodeConversation.tsx:674-708`); Running now; finish notice. | Collapsed receipt LEADS with ✓ / ⚠ in its tone (`CodeConversation.tsx:692-701`, `lib/buildApp.ts:1571-1574`), "2 min 14 s" in the app's words (`buildApp.ts:1562-1564`), "N failed" said apart (`:1581-1590`); `doneWords` for a non-clean end. | Changes tab "This message" / "Whole session". | Two-press "Revert this file"; Retry / Sign in / Continue on another account; carry-on after a restart. | Complete |
| **Mission** | Headline Finished / Could not finish / Stopped / Needs you / Part failed; bell "Your objective is done / failed / needs you". | Receipt now "Answered by <model> · cloud · 4 min 12 s · 23 actions" (`components/agents/mission/MissionOutput.tsx:362-395`). | Deliverable panel; "Open the full run". | Ask for changes / Run it again / Retry the N failed items; the chat's ApprovalCard inline (`MissionOutput.tsx:54`, `:174`, `:623-632`). | Complete |
| **Workflow run** | Run history badge in the one table's words incl. "Part failed" (`app/workflows/page.tsx:793-802`); Running now lists live runs (`lib/runningNow.ts:357-446`); bell rings a failed run. | Badge; the why is only inside the expanded row (step outputs, notes `:866-875`). | Steps' outputs in the expand; "N sessions" count. | **Stop on every live row** (`:820-841`); Resume on interrupted (`:803-817`); no "Run again" on a failed row (canvas only). | Mostly complete |
| **Schedule fire** | Schedules row word + detail inline (`app/schedules/page.tsx:1105`, `lib/statusWords.scheduleRunWords`); bell attention + one ping for a failed/short run; tray. | Done / Needs you / Part failed / Failed / Skipped / Missed; Run now's amber note (`:998`). | Via the run it started. | Run now; On/Paused switch; Edit. | Complete |
| **Rule fire** | Row "Fired N× · last <time>" (`app/reflex/page.tsx:557-562`); `reflex.failed` rings bell / tray / phone (`NotificationBell.tsx:267`). | "could not start: <reason>" on the row (`:563-570`); a source that is off says so (`:572`). | Via the session / run it started — **no link from the row**; the Test result reads "Fired session · session <id>." with raw kind and id (`:517-522`, `:633-639`). | Test; Edit; Delete. | Partly worded |
| **Media job** | Chat line "Your image is ready." + the picture (`media/jobs.py:143-155`); bell "Your image is ready" with the charged note (`NotificationBell.tsx:968-990`); Running now while in flight (`runningNow.ts:386-404`). | "Your <kind> could not be made: <reason>" (`media/jobs.py:158-164`). | The file in `documents` (files rail), the Creative library. | **No Try again** on a failed media line (the user asks again); a spend is never undoable. | Mostly complete |
| **Night task** | ONE morning notice (`build.night_summary`); Last night list (`components/harness/LastNightList.tsx:13-94`); Running now while running (`runningNow.ts:416-430`). | Done / Needs you / Failed / Stopped badge (`LastNightList.tsx:83`); "main folder changed" un-folded (`:94`). | Changes (Branch view), commit on its own branch. | Push / Open a pull request / Throw away (two presses); re-queue by hand. | Complete |
| **Phone message** | "On it — I'll send the result here." (`comm/inbound.py:71-75`) then the run's summary on the phone and in the thread (`:497-525`). | The run's own summary; a crash "I hit a problem: <plain reason>" (`:510`). | **Not named**: the summary is the model's words; files are not listed; a summary-less run reads "(no result)" (`:497`). | None from the phone (send again). | Partly said |

---

## 2. Per-item table (the 14 checklist workflows)

| # | Workflow | Mark | Move vs v1.447.0 | Evidence (file:line) | Experience at the end | Missing | Smallest fix · size |
|---|---|---|---|---|---|---|---|
| 1 | **Chat request** | **PASS** | held | `WorkLine.tsx:1037-1051`; `TurnReceipt.tsx`; `page.tsx:13318` (chat-reply-live), `:13407-13419` (chat-notice with ×), `:4457` + `:12160-12167` (chat list "couldn't load. Try again") | Prose + "Worked for …" ✓/⚠ + "answered by … · cloud"; screen readers hear "Reply ready." | Baseline's small item (ease 46) is closed. | — |
| 2 | **Coding task — Chat › Code** | **PASS** | PARTIAL → PASS (code, v1.452.0) | `HarnessStartComposer.tsx:10`, `:262` (copy off by default); `lib/buildGit.ts:42-54` ("Work in a separate copy of the folder", "Your folder stays as it is until you press Keep these changes"); `HarnessBranch.tsx:252-309` (one press = commit + merge, the daemon's own refusal, a commit that landed before a refused merge is said) | Work lands in the folder the user picked; the reply line says what ran and whether checks passed. | With the copy ON, "Keep these changes" lives only in Changes › Branch; the next-step box under the reply still offers Commit / Open a pull request, not Keep (`lib/nextStep.ts` git offers). | Offer "Keep these changes" as the next-step box's git button when the copy is on · S |
| 2b | **Coding task — Build › Code** | **PASS** | held (improved: glyph + words) | `CodeConversation.tsx:692-701`; `lib/buildApp.ts:1562-1590` | "✓ answered by Opus 5.5 on Work · 2 min 14 s · 7 actions · 3 files changed" / "⚠ … · 1 failed". | The glyph is `aria-hidden`; a screen reader gets the line without the verdict unless a step failed. | `aria-label` "Finished" / "Needs a look" on the summary · S |
| 3 | **Tool call** | **PASS** | held | `lib/stepWords.ts`, `WorkLine.tsx`; `StepDetailPanel` | Each call worded, failed ones counted. | — | — |
| 4 | **MCP / tool-pack installation** (Tools, Directory, chat) | **PASS** | held (+ chat door) | `ToolsPage.tsx` states; `DirectoryPage.tsx`; `settings/records_tools.py:1045-1065` ("Added the tool server 'x' (on, N tools). Jarvis can use its tools now." / "…but it did not start<why>", waiting-for-key words) | Honest started / not started with why at every door. | — | — |
| 5 | **Connection setup** | **PASS** | held (half-gap closed) | C1: Settings › Connections and the chat secure card test before saving (`settings/credentials.CredentialStore.store` → `routes.connections.check_key_first`, "not_saved" words); `OAuthAppForm.tsx`; `default_replacement.py`; memory base read back (`components/memory/LongTerm.tsx:484-493`, `:1387`) | "Added and answering." / "Nothing was saved" / the default names its replacement before a removal. | A web service added from chat is not checked: "Added the web service … Jarvis can call it with rest_call now" (`records_tools.py:163`, `:1257`) — no reachability test, and the tool name `rest_call` in a sentence the model relays. | Run the page's Test from `web_service_add` and say "answering" or why not; drop "with rest_call" · S |
| 6 | **Browser task — sidebar** | **PASS** | held | `extensions/chrome/src/sidepanel.ts` fold summary; `browser/panel.py` route notice | "4 steps · 1 failed · 12 s". | — | — |
| 6b | **Browser task — from the chat page / an agent run** | **PASS** | PARTIAL → PASS (code, v1.449.0, G54) | `daemon/routes/chat.py:5110-5200` (`_risk_ask`: once-only card, reason "page", no clock, Stop checked, Allow = one call, Deny = refusal in words); `core/risk_door.py:115-178` (no queue id in any refusal: `refusal_words`); `browser/tools.py:1399-1414` | A payment / password / destructive step shows a card in the chat; Allow runs that ONE step; no "Pending approval id". | — | — |
| 7 | **Computer-use task** | **PASS** | PARTIAL → PASS (code, v1.449.0) | `computeruse/tools.py:247-300` (the chat's or run's resolver, approve AND consume in one breath, deny on a cancel) | Same card as 6b from chat; the step resumes. | Residue: a call from a lane that cannot ask (POST lane / unattended) leaves its queue row PENDING (`computeruse/tools.py:284-293`), and approving it on the Browser page still says "Approved. Ask the agent to try that step again." (`BrowserPage.tsx:460`). `new_harness` (the standalone loop that blocks on `awaiting_approval`) has no caller. | Deny the row when nothing can ask (as the chat lane does) so no stale approval waits · S |
| 8 | **Agent delegation** | **PASS** | held | `AgentRunRow.tsx:170`, `:437`; `RunResultCard.tsx:13-26`; `HarnessHelpers` failure words | "The researcher worked for … · part failed"; Try again / Revert / Keep going. | — | — |
| 9 | **Mission** | **PASS** | PARTIAL → PASS (code, v1.449.0) | `MissionOutput.tsx:362-395` (`mission-receipt-took`, `mission-receipt-actions`), `:54`, `:174` (chat ApprovalCard) | "Answered by … · cloud · 4 min 12 s · 23 actions". | — | — |
| 10 | **Remote-machine task** | **PARTIAL** | PASS → PARTIAL (stricter reading; code unchanged) | Mission path: `agents/mission.py:1538-1568` (`_remote_reports` ends the card done). @-round path: chat-bound room mirrored (`daemon/routes/agents.py:3225-3263`, `app/chat/page.tsx:5975-5990`). **Agent-run hand-off**: `agents/remote.py:830-870` (`open_handoff_room` makes a room with NO `chat_thread_id`), so the report is not homed (`routes/agents.py:3238-3239`) and `remote.message` is in no bell list (`NotificationBell.tsx:221`, `:282`; only `lib/eventWords.ts:128` on Activity). | Inside a mission and an @-round: "accepted … will report back", then the report. From a chat-started agent run that handed work to a remote: the reply says "accepted and will report back", and the report never appears where the user is — only an Activity row "A remote agent messaged back". | A report-back for a non-mission hand-off has no home. | Stamp the room with the run's `chat_thread_id` (options) so the existing inbound homing appends it to the chat; else a bell row "<remote> reported back · Open" · S |
| 11 | **Scheduled / background task** | **PASS** | held (both baseline gaps closed) | `runningNow.ts:357-446` (workflows, media, night); `app/workflows/page.tsx:820-841` (Stop on every live row); schedules row words; `reflex.failed`, `autonomy.proposed` in the bell | Every background kind is in Running now, can be stopped, and rings when it fails. | Rule Test result "Fired session · session <id>." raw kind + id, no link (`app/reflex/page.tsx:517-522`, `:633-639`); a rule row does not link to the run it started; a failed workflow row's reason is only in the expand and has no "Run again". | Word the Test result ("Started a task · Open") and link the row's last fire · S |
| 12 | **File creation / modification** | **PASS** | held | `ReplyChanges.tsx`, `ChangedFiles.tsx`; `change_claims.py`; Build › Code Changes; Chat › Code now writes into the picked folder | "N files changed" + Undo; honest notes. | Phone runs do not name files (trace above). | — |
| 13 | **Failure / retry** | **PASS** | held | `lib/failureLine`, `lib/failureActions`, `HarnessFailure.tsx`, `CodeConversation.tsx` failure presses; `components/LoadFailedLine` on eleven more pages (v1.449.0) | Every primary failure says what failed and offers the press. | ~25 PRESS failures on secondary pages still print the server's text: `app/skills/page.tsx:226` (`err.message` → `:663`), `app/artifacts/page.tsx:290`, `:331`, Fleet, Updates, Browser save, Creative (0i "left open"). A failed media line has no Try again. | Route those `ErrorNote`s through `failureOf()` / `couldnt()` · S–M |
| 14 | **Approval flow** | **PASS** | held (improved) | `ApprovalCard` `onRedirect` ("No, tell it what to do instead"); `dockAsk.ENTER_BLOCKED_NOTE` now in PaneAsk too; risk card (6b); `lib/chatControlWords.ts:113` (a ticked tool "runs in this chat without asking you first", item 9) | Card → Allow → the step row runs; the receipt lists approvals; a ticked tool says up front it will not ask. | — | — |

### Chat-first configuration (6)

| Workflow | Mark | Move | Evidence | Note |
|---|---|---|---|---|
| Change a setting | **PASS** | PARTIAL → PASS (code, v1.448.0) | `lib/configCards.ts:25-26`, `:71-72`, `:123`, `:137-142`; `ConfigCards.tsx:68-69` | "Ask before commands → Auto", links to the row. |
| Add a destination | PASS | held | `routes/comm.two_way_note` | — |
| Queue a night task | PASS | held | `records_build.py` | — |
| Add a model server | PASS | held | `records_connections.py` | — |
| Add an account | PASS | held | `records_connections.py` | — |
| Remember a preference | PASS | held | `TurnReceipt` "Remembered: …" | — |

### Ease-of-use review items in this section

| Item | State | Evidence |
|---|---|---|
| 3 key saved before tested | **done** | Settings › Connections `?check=1`; secure card `check_key_first` (C1) |
| 12 Chat › Code hidden copy by default | **done** (v1.452.0) | `HarnessStartComposer.tsx:262`; `lib/buildGit.ts:42-54`; `HarnessBranch.tsx:252-309` — Keep only in the Branch view (see #2) |
| 19 screen reader never hears a reply | **done** | `page.tsx:13318` |
| 20 rule failure tells nobody | **done** | `reflex.failed` (`NotificationBell.tsx:267`) |
| 21 watcher suggestions silent | **done** | `autonomy.proposed` (`:271`) |
| 22 workflow with failed step says Done | **done** | "Part failed" (`statusWords`) |
| 30 Stop only from the canvas | **done** | `app/workflows/page.tsx:820-841` |
| 31 step arguments lost silently | **done** | `NodeInspector.tsx:8`, `:47`, `:83-85` (`jsonProblem`, role=alert) |
| 36 config cards show internal values | **done** | `lib/configCards.ts:123` |
| 41 chat list failure looks empty | **done** | `page.tsx:4457`, `:12160` |
| 46 small errors look like a broken reply | **done** | `page.tsx:13407-13419` |
| 57 memory base "Source added" | **done** | `LongTerm.tsx:484-493`, `:1387` |
| 66 Spotlight "Task done" | **done in part** | `desktop/main.js:3018-3044` (outcome words); "Open folder" is an action on macOS only (`:3056-3089`); on Windows the click opens the session page |
| 70 Activity raw event names | **done** | `lib/eventWords.ts` (105 rows), `TimeTravelFeed.tsx:28`, `:618` |
| 74 (Running now part) | **done** | `lib/runningNow.ts:30-68` |

---

## 3. Category scores (this lane's view)

**Execution visibility: 93** (v1.447.0: 88). Up because every kind of background work is now in Running now (workflow runs, media jobs, night tasks), every live workflow row can be stopped, the Activity page words all 105 event types, a screen reader hears a finished reply, and a chat list that failed to load says so. Held under 95 by: a remote's report-back from an agent-run hand-off lands nowhere visible; a rule row does not link to what its fire started and its Test result prints a raw kind and id; a failed workflow's reason is only inside the expanded row; a phone run's result does not name its files.

**Action clarity: 94** (v1.447.0: 89). Up because a folder-first coding session writes into the user's own folder (and "Keep these changes" is one press when the copy is on), the website risk step is a card that resumes on Allow, the mission receipt says how long and how much, Build › Code's receipt leads with ✓/⚠ in the app's duration words, Spotlight says Needs you / Part failed, and setting cards show names. Held under 95 by: about 25 press failures on Skills, Artifacts, Fleet, Updates, Browser and Creative still print the server's text; no Try again on a failed media line or "Run again" on a failed workflow row; the next-step box offers Commit / Open a pull request instead of "Keep these changes"; `web_service_add` claims "Jarvis can call it now" without a check.

---

## 4. Remaining work (impact × frequency × confusion)

| # | Item | Smallest fix | Size | Type | Evidence |
|---|---|---|---|---|---|
| 1 | **A remote's report-back from an agent-run hand-off is invisible** (chat says "will report back", nothing arrives). | Carry the run's chat thread into `open_handoff_room` so the inbound route's existing homing appends the report; otherwise ring the bell for `remote.message` with "Open". | S | backend + UX | `agents/remote.py:830-870`; `routes/agents.py:3225-3263`; `NotificationBell.tsx:221`, `:282` |
| 2 | **~25 press failures print the server's raw text** on Skills, Artifacts, Fleet, Updates, Browser, Creative. | Route each `ErrorNote` through `lib/loadFailed.failureOf()` / `couldnt()`. | S–M | UX | `app/skills/page.tsx:226`, `:663`; `app/artifacts/page.tsx:290`, `:331` |
| 3 | **Rule Test / last fire don't say what started** ("Fired session · session <id>."). | Word the kind ("Started a task", "Ran the workflow X") and link to `/sessions/<id>` / `/workflows`; link the row's last fire. | S | UX | `app/reflex/page.tsx:517-522`, `:557-562`, `:633-639` |
| 4 | **Next-step box offers Commit / PR, not "Keep these changes"** when the separate copy is on. | Map the git offer to Keep (the Branch view's own `keep()`) for a non-programmer session; keep Commit / PR in the pane. | S | UX | `lib/nextStep.ts`; `HarnessBranch.tsx:258-280` |
| 5 | **A failed workflow row hides its reason and has no "Run again"; a failed media line has no Try again.** | Show the first failed step's words on the collapsed row; add "Run again" (POST /workflows/run with the same name) and, on a failed media line, a Try again that re-sends the request. | S | UX | `app/workflows/page.tsx:793-841`; `media/jobs.py:158-164` |
| 6 | **`web_service_add` is not checked and names `rest_call`.** | Run the page's Test; say "added and answering" or why not; drop the tool name. | S | UX | `settings/records_tools.py:163`, `:1257` |
| 7 | **A phone run's result does not name its files; "(no result)" fallback.** | Append "Saved: a.xlsx, b.pdf" from the session's ledger documents; replace "(no result)" with the plain "finished without a summary" words. | S | backend | `comm/inbound.py:497-525` |
| 8 | **A computer-use queue row left PENDING by a lane that cannot ask** still ends "Ask the agent to try that step again." | Deny the row when no resolver exists (no stale approval), and drop the sentence. | S | backend | `computeruse/tools.py:284-293`; `BrowserPage.tsx:460` |
| 9 | **Build › Code receipt verdict is visual only** (glyph `aria-hidden`). | An sr-only "Finished" / "Needs a look" in the summary. | S | a11y | `CodeConversation.tsx:694-699` |
| 10 | **Spotlight "Open folder" is macOS-only.** | On Windows, put the folder path in the toast body or open the folder on click when the run made files. | S | desktop | `desktop/main.js:3056-3089` |

---

## 5. Regressions

**None found in code.** Every v1.447.0 PASS still holds; the four v1.447.0 PARTIALs moved to PASS by code changes (v1.448.0, v1.449.0, v1.452.0). The one new PARTIAL (remote-machine task) is a stricter reading of unchanged code: the baseline judged it on the mission card; this lane also traced an agent run's hand-off, whose report-back room (`remote.py:830-870`, v1.420.0) was never bound to a chat.

Things checked for regressions and found intact: the hand-off row's settle (`AgentRunRow.tsx:170`), the failure line's presses, the bell's one-notice rows, the Schedules words, the sidebar fold, the risk door's no-id refusal on every path (`grep "Pending approval id"` only finds a regex that strips it, `browser/tools.py:2349`), the v1417 no-`why` ApprovalCard (redirect is only on the compact card), the branch default change (Try on still keys off git, not the switch).

---

## 6. Never proven live (this lane read code only)

- The chat-page risk card on a real site (payment / password step) and that Allow resumes exactly one action.
- "Keep these changes" against a real repository with a checked-out base (commit + merge, a refused merge after a commit).
- A real remote answering 202 and reporting back — in a mission, an @-round, and from an agent run (the gap above).
- Running now with a live workflow run, a media job and a night task at once; Stop on a schedule-started workflow.
- The Spotlight outcome toast and every tray notice as real Windows toasts in the packaged app.
- "Reply ready." with a real screen reader.
- Build › Code receipts with the real CLIs in the packaged app.
- Media jobs against real Pixio / OpenAI (spends money).
- A phone run's summary on a real phone.
