# Iron Jarvis Product-Readiness Audit: Final Report (v1.350.2, HEAD 94043cfb) and Re-audit (v1.357.0)

**What this report is built on.** Seven audit lanes covered sections A to O. Each lane's PARTIAL and FAIL findings were then re-checked against the code by a hostile verifier, and against the running app using read-only requests. Where the auditors and verifiers disagreed, the verifier's version is used. Claims the verifiers disproved are left out, for example the bell's "Computer-use run failed — awaiting_approval" row, which is dead code because nothing publishes that event.

This synthesis re-confirmed six anchors directly in the tree:
- `system_map.py:242` counts `last_status == "failed"`, a value the scheduler never writes.
- `platform.py:2023-2036` and `:2112` record a schedule run as "ok" from its status alone.
- `RunResultCard.tsx:68` treats a cancelled run as failed.
- `NotificationBell.tsx:175` rings for missions only.
- `_FILE_WRITING_TOOLS` (`chat_turn.py:4119`) leaves out the image, PDF-split, edit and rename tools.
- Both chat lanes tell every model "You run locally on the user's machine" (`chat_turn.py:5640`, `routes/chat.py:2954`).

The checklist has **213 items: 41 PASS, 169 PARTIAL, 3 FAIL.**

---

## 0. Re-audit after the build waves (v1.357.0, 2026-10-19)

**What was re-checked, and how.** Seven build waves (v1.351.0 to v1.357.0) shipped 83 of the 86 gaps in section 9. The owner chose not to build G83, G85 and G86. Seven re-audit lanes then re-marked all 213 checklist items against the code at HEAD `3fbd606a` (v1.357.0). Each lane covered a group of sections (A-B, C-D-O, E-J, F-K, G-N, H-I-M, L) and:
- re-read the code behind every item and gap in its sections, and checked each gap against its acceptance;
- ran the pinned tests for those areas one file at a time. All were green: for example 139 pytest and 209 vitest tests for L, and 39 pytest and 105 vitest tests for A-B;
- sent read-only GET requests to the running app. It reports v1.354.0, so live checks cover waves 1 to 4 only;
- ran read-only probes where reading the code was not enough: the tool selector, the failure-words classifier, and a side-by-side run of the v1.350.2 and HEAD tool selection.

This synthesis then checked the lanes' contested claims directly in the tree. Two of them did not hold:
- Lane A-B reported that a mission with failed worklist items reads "part of it failed" on its screen while the bell says "Your objective is done". The code rules this out. `agents/orchestrator.py:1727-1743` turns that mission's verdict into `completed_with_failures`, `:1421` stores it on the session, and `:557` puts it on the bell's payload. G09 is counted as met, and this is not listed as a regression.
- Lane A-B also listed the bell's "Computer-use run failed — awaiting_approval · 4 steps · run_…" row as a live leak. It is still dead code, as the first audit found. Only `ComputerUseHarness._finish` publishes `computeruse.run_finished` (`computeruse/harness.py:379-397`), and the one place that builds that class, `new_harness` (`computeruse/tools.py:54`), has no caller. The row would still call an approval wait a failure if that code were ever wired up.

Where lanes proposed different scores for a category, I used the median, rounded down. Where they disagreed about a gap, I took the stricter verdict unless the code settled it.

### 0.1 Overall score: **70 / 100** (was 55)

The twelve categories average 70.8. I score 70, not 71, for two reasons:
- Several of the larger new pieces have never run against the real service: Microsoft 365, GitHub's remote server, the Gemini key, the Jarvis-to-Jarvis door and the web-service call.
- v1.355.0 to v1.357.0 are not yet on the user's install.

Most of what kept the first audit at 55 is now fixed in code:
- Schedules and missions report the ledger's verdict.
- The bell rings for unattended failures and remembers them after a close.
- Failures read in words with a press that fixes them.
- A pack is green only when it works.
- Import finds what the user already has.
- Emergency stop really stops self-started work.

130 of 213 items now pass, up from 41, and none fail. What holds the score at 70 is a set of narrower problems that people will still meet:
- The Schedules row leads with "Done" again.
- A web question that names an app no longer searches the web.
- Chat may miss GitHub's pull-request tools.
- A Claude Code timeout gives an anonymous failure line.
- One helper run shows two receipts.
- The desktop shell still says "daemon".

### 0.2 Category scores

| Category | Old | New | Why |
|---|---|---|---|
| Action clarity | 60 | 77 | Schedules, missions, the helper-agent card, the work line and the browser sidebar now report the ledger's verdict, and a Stop reads "Stopped". Still wrong: the Schedules row leads with grey "Done", a scheduled workflow parked on a question reads "Done", and a Build pane can only say "take a look". |
| Execution visibility | 62 | 76 | Running now is one press from the orb, the bell rings for unattended failures and replays what happened while the window was closed, and a finished agent run keeps its actions, model and folder. Still silent: a chat ask while the user is on another page of the same window, and a helper run that finishes after the user left the chat. |
| Connections | 42 | 58 | Status is honest, setup needs show before Connect, and there is one catalog and one door, plus On/Off, Change key, Microsoft 365, a Gemini key and one-press Ollama. Gmail and Drive still need a Google developer setup. Chat arms at most 24 tools per app. Directory API-key cards say "connected" without a test, LM Studio is not detected, and another Jarvis cannot be reached between two packaged PCs. |
| Import/discovery | 45 | 74 | Discovery runs by itself on the Directory. Current VS Code and project files, plugins, Windsurf and Gemini CLI are read, each app's off switch is respected and secrets are vaulted. Nothing appears at first run, and servers outside the catalog read only "A tool from <app>". |
| First-run experience | 52 | 70 | The release page explains the download and the Windows warning, the demo says it is a demo, Ollama is one press, and the choice confirmation stays. A user with no key and no CLI still needs Ollama and a model download of about 2 GB. |
| Error/recovery UX | 60 | 70 | Failures have a plain headline, folded detail, a fix press and a "not affected" line, and recovery is announced. A failure the classifier does not recognise is anonymous in chat and raw elsewhere, and the desktop shell says "daemon". |
| Consistency | 40 | 60 | There is one status table and one approval vocabulary. One helper run still shows two receipts in different words, "Task complete" sits beside "Done", and "part failed" is phrased four ways. |
| Discoverability | 50 | 63 | Everything is tiered, phone chat and self-care can be found, and "What can you do?" reaches the Guide. Chat never suggests a mission, and phrasings that name no app find nothing. |
| Beginner friendliness | 45 | 63 | "Daemon" is gone from the banner, chat and Settings, and protocol words sit in folds. The tray and notifications still say "daemon", the sign-in fix says to open a terminal, and model rows read "subscription" and "fleet". |
| Trust | 55 | 70 | Emergency stop stops self-started work, cloud models are not told they are local, reading anywhere is disclosed and can be limited, and every ask says whether it can be undone. The run page never says local or cloud, and a reply from the user's own model server never names the machine. |
| Stability | 85 | 92 | All 14 stability items pass. Small gaps remain: an @-agent round has no Stop, and a failed unattended run raises no OS toast while the window is in the tray. |
| Marketplace/capability readiness | 58 | 76 | One catalog file and one add/remove door. Requirements, permissions, On/Off and self-description are carried as data. Models, agents, channels and services still each have their own row shape. |

Where lanes disagreed: Connections came out 66, 58 and 55 (the two lower lanes found the 24-tool cap and the untested "connected"). Error/recovery came out between 66 and 80, Consistency between 56 and 66, Execution visibility between 74 and 79, and Trust 67 and 74. Each score is the median, rounded down.

### 0.3 PASS / PARTIAL / FAIL (first audit → now)

| Section | Items | PASS | PARTIAL | FAIL |
|---|---|---|---|---|
| A. Actions self-evident (incl. 12 surfaces) | 33 | 3 → 13 | 30 → 20 | 0 → 0 |
| B. Execution receipt | 24 | 10 → 22 | 14 → 2 | 0 → 0 |
| C. Connections | 21 | 2 → 10 | 17 → 11 | 2 → 0 |
| D. Import | 16 | 1 → 13 | 15 → 3 | 0 → 0 |
| E. First run | 11 | 1 → 5 | 10 → 6 | 0 → 0 |
| F. Natural-language discovery | 6 | 0 → 0 | 6 → 6 | 0 → 0 |
| G. Error & recovery | 10 | 4 → 7 | 6 → 3 | 0 → 0 |
| H. Consistency | 15 | 2 → 9 | 13 → 6 | 0 → 0 |
| I. Engine room | 14 | 2 → 10 | 11 → 4 | 1 → 0 |
| J. Discoverability | 5 | 0 → 2 | 5 → 3 | 0 → 0 |
| K. Trust | 11 | 1 → 5 | 10 → 6 | 0 → 0 |
| L. "Did it work?" | 14 | 2 → 9 | 12 → 5 | 0 → 0 |
| M. "What does this mean?" | 8 | 0 → 3 | 8 → 5 | 0 → 0 |
| N. Stability | 14 | 11 → 14 | 3 → 0 | 0 → 0 |
| O. Marketplace readiness | 11 | 2 → 8 | 9 → 3 | 0 → 0 |
| **Total** | **213** | **41 → 130** | **169 → 83** | **3 → 0** |

Notes on the counts:
- **Source.** Every count comes from the lanes' item-by-item marks, and every item was marked. Two lane summaries gave different totals from their own items: C-D-O said 24 pass where its items say 31, and H-I-M said 20 where its items say 22. The item marks are used.
- **Two A marks that may be too low.** "Did something partially succeed?" and "Did it fail?" are PARTIAL only because of the two claims in the method note that did not hold. Corrected, A would be 15 PASS / 18 PARTIAL and the total 132 / 81 / 0. The table keeps the lanes' marks.
- **One item moved backwards.** G "Jarvis identifies what failed" went from PASS to PARTIAL (regression 4 below).
- **B "Harness" passes while A "Which agent/harness?" is PARTIAL.** This is not a contradiction. The receipt records the harness and shows it on expand, but chat never says it in words on the line.

### 0.4 Gaps whose acceptance is not fully met

75 of the 83 built gaps meet their acceptance. These 8 are partly met:

| Gap | What works now | What remains |
|---|---|---|
| G01 Schedules record real outcome | The outcome is recorded from the ledger (`platform.py:2074-2097, 2214`). The notice says "finished, but it needs you", Run now is amber, and the bell, Status map and home line count short runs. | The row reads grey "Done · needs you" / "Done · part failed", with only an amber dot (`lib/statusWords.ts:91-96, 142-145`; `ui.tsx:207-260`). A run that failed after its ask expired reads "Done · needs you" beside a "Failed" badge (`platform.py:2088`). A parked scheduled workflow and a skipped goal iteration record "ok" (`platform.py:2111-2115, 2151`). Lanes A-B, G-N and H-I-M called it met; lane L, partly met. |
| G15 Connect in chat works and continues | Saving a token says "GitHub connected · N tools", turns the app on for the chat, and Continue re-sends the task. | Chat arms the first 24 tools of an app in name order (`chat_turn.py:127, 3160, 3216`; `registry.py:420`). On GitHub's maintained server the pull-request tools probably fall past the cut. Tested with a local stand-in only. |
| G19 Helper card/row in words | "answered by <model> · 10 min 57 s", a neutral Stopped, error rows in words, and the row says where and on what. | The new "What it did" fold reads "Builder used history search" (`agents/mission.py:255-257`). |
| G30 "Iron Jarvis", not "daemon" (shell and chat) | Banner, chat, Status, splash and Settings say "Iron Jarvis isn't responding". | The tray tooltip, Windows notifications and startup-failure dialog title still say "daemon" (`desktop/main.js:955-964, 1574-1580, 3681`). |
| G39 Chat knows apps by name | Chat recognises an app by its everyday name, says whether it is connected, and ask-arms its tools. | It works only when the message names the app, and only in the stream lane. The 24-tool cap applies. Its web rule broke ordinary web research (regression 2). |
| G41 Refusals leave a fix button | A browser setup code leaves "Pair your browser", and an app tool's refusal names the app and where to turn it on. | The Drive button appears only if the model calls `app_connect`. A refused built-in tool still reads "_Note: write_file could not run (not turned on for this chat)._" (`chat_turn.py:2426-2429`). |
| G61 Remote Jarvis node | A token-guarded task door, one low-trust run per task, one reply home from every ending, and an address card. Tested with two real app factories on one machine. | The packaged app listens on 127.0.0.1 only (`desktop/main.js:3583`). The card tells the user to run `ironjarvis serve --host <ip>` and set `IRONJARVIS_HOST_ALLOWLIST` (`daemon/routes/agents.py:98-108`), which the desktop app cannot do. The register form still asks for "http-task". |
| G76 One vocabulary | `VOCABULARY.md` is the canon, and a scan fails on a retired word in any dashboard string. | Daemon and desktop strings are outside the scan: "MCP pack <name>" in Secrets, "the daemon log", and "daemon" in the tray. "Session" stays as a second name for a task, on purpose. |

Two gaps that one lane called partly met are counted as met:
- **G03 (bell rule for unattended work).** Unattended runs ring and are remembered. The case lane A-B raised, a chat ask while the user is on another page of the same window, belongs to G05. G05's acceptance covers a chat that no window follows, and that works. The in-window case is still silent (`routes/chat.py:1036-1040`) and is listed under A "Does Jarvis need something from me?".
- **G09 (mission headline partial).** See the method note: the bell and the screen agree in the code.

### 0.5 Regressions found

1. **The Schedules row leads with "Done" again.** v1.351.0 shipped an amber "finished · needs you" (`git show 9c3c9c11:dashboard/lib/scheduleOutcome.ts:40-41`). The one status table (v1.352.0) and the calm default badge (v1.356.0) turned it back into grey "Done · needs you". A run that FAILED after its ask expired now reads "Done · needs you", and its notice says "finished, but it needs you", while its own badge says "Failed" (`platform.py:2088`; `SessionStatusBadge.tsx:56-59`). A test pins this as intended (`tests/test_schedule_outcomes_v13510.py:503`).
2. **Confirmed: a web question that names an app no longer searches the web.** "look up the stripe api docs for refunds", "search for notion templates for a CRM" and similar requests armed web search at v1.350.2 and arm nothing now. The apps section then tells the model "Never search the web in its place" (`tools/autoselect.py:1060-1116`; `connectors/app_names.py:420-427`).
3. **Plausible, not run live: GitHub's pull-request tools are probably out of chat's reach.** The card moved to GitHub's own, larger server in v1.355.0, and chat arms at most 24 tools per app in name order (`chat_turn.py:127`). The Microsoft 365 card filters its tools for exactly this reason (`catalog.py:395`); the GitHub card does not (`catalog.py:230-253`).
4. **Confirmed: a failure the classifier does not recognise is anonymous in chat.** A Claude Code CLI timeout reads "Iron Jarvis couldn't finish this answer. Press Retry…", where the old raw line at least named claude-cli and the timeout (`providers/failure_words.py:262-341`). The run page, schedule row and bell still print it raw: "…(claude-cli: claude-cli: CLI timed out — no output for 300s)".
5. **One helper run shows two receipts.** The settled row's new body and the boxed card under the reply say the same facts in different words: "Ran on" vs "answered by", "Open run" vs "Full transcript", and "Task complete" vs "Done" (`app/chat/page.tsx:3149-3178`).
6. **The card's "What it did" fold shows tool ids**, such as "Builder used history search" (`agents/mission.py:255-257`).
7. **"✓ Answered in N s" sits over a cut-off reply**, above "The reply was cut off." (`WorkLine.tsx:216-219, 896-908`).
8. **The Directory contradicts itself.** "Files & folders" and "Fetch web pages" have Connect buttons under the line "Built in, nothing to connect" (`catalog.py:283, 516`; `DirectoryPage.tsx:1176, 1376`).
9. **The Directory's "N connected" counts apps that are off or did not start** (`DirectoryPage.tsx:1242, 1255`; `service.py:190-207, 232-245`).
10. **New bell rows print raw markdown summaries**, such as "# Final Report ## What happened …" (`NotificationBell.tsx:276-281, 324-325`).
11. **The new `rest_call` tool reads "Ran rest_call"** on the work line (`lib/workTarget.ts:49-80`).
12. **Minor:**
    - The calm ConfirmButton default dulls the armed second press of Sessions' bulk delete (`app/sessions/page.tsx:306-311`).
    - `README.md:94` still names the old "Simulated mode" strip.
    - New bell rows hard-code amber classes (`NotificationBell.tsx:1055, 1082`), and `ConnectDoors.tsx:665` uses `text-rose-300`, so a custom palette does not re-colour them.

None of the waves added boxed cards to the calm chat. The new home, bell and receipt pieces are plain lines and folds.

### 0.6 Engine-room leaks remaining

On paths a typical user reaches:

| Where | What the user sees |
|---|---|
| `desktop/main.js:955-964, 1574-1580, 3681` (tray, Windows notification, startup dialog title) | "Iron Jarvis — daemon did not start", "The daemon stopped answering repeatedly…" |
| `providers/cli_auth.py:50-58` (SIGN_IN_FIX, on Status doctor rows and in refusals) | "Open a terminal, run `claude`, then `/login`, then click Test on Connections." |
| `orchestrator.py:2077` via `failure_words.py:363-392` (run page, schedule row, bell body) | "…(claude-cli: claude-cli: CLI timed out — no output for 300s)" |
| `scheduling/run_outcome.py:65` | "The run failed, and it gave no reason (the daemon log has the details)." |
| `chat_turn.py:2426-2429` | "_Note: write_file could not run (not turned on for this chat)._" |
| `chat_turn.py:4927-4928, 5018, 5023-5024` | "Ask again and arm a document tool (the “+” menu, or keep Auto-tools on)" |
| `agents/mission.py:255-257` (card's "What it did") | "Builder used history search" |
| `components/ModelRowBits.tsx:25-28` (model menu) | "subscription", "fleet", "spark/fleet" |
| `ConnectionsPage.tsx:1672-1696` | "Claude Code CLI", "Codex CLI", "Grok CLI" |
| `lib/workTarget.ts:49-80`; `computeruse/tools.py:271-277` | "Ran web_action", "Ran rest_call", "Pending approval id=<id>." |
| `lib/toolWords.ts:15-68` (approval card under Ask first) | "The assistant wants to run write_document" |
| `desktop/main.js:2625-2643` (desktop ask toast) | "use edit file", "use mcp  github  create issue" |
| `agents/orchestrator.py:2172` | "Session cancelled by the user." |
| `providers/adapters/mock.py:177-180` | The demo reply opens "Done. Wrote RESULT.md summarizing the task." (the note after it corrects this) |
| `media/pixio.py:207-212`; `media/pixio_http.py:37-40` | "update the 'pixio' secret or PIXIO_API_KEY" |
| `SecretsPage.tsx:211, 218` (names from `mcp/secrets.py:112, 245, 254`) | "mcp.github.GITHUB_PERSONAL_ACCESS_TOKEN", "MCP pack github: …" |
| `settings/schema.py:164, 250, 299, 316, 340, 380`; `lib/nav.ts:249, 284, 415` | "Read a project's AGENTS.md and CLAUDE.md in chat", "Autonomy (the pulse)", "Sentinels", "Reflexes", "Artifacts" |

Advanced or rare paths:

| Where | What the user sees |
|---|---|
| `daemon/routes/agents.py:98-108` (Another computer card) | "ironjarvis serve --host <ip> … IRONJARVIS_HOST_ALLOWLIST=<ip>" |
| `components/agents/SetupCard.tsx:1871-1873, 2363-2380` | "http-task (task API)", "openai-chat (chat/completions)" |
| `connections/RestHookups.tsx:181-185` | A raw "Config (JSON)" box |
| `connections/specs.py:254, 272`; `ConnectionsPage.tsx:1052-1058` | Google Cloud / Entra OAuth client steps; "<provider>_oauth_client_id" |
| `components/memory/baseCatalog.ts:96-99` | "An MCP memory server … Claude-Desktop-style config" |
| `mcp/tools.py:752-755, 767, 784` | "Restart Iron Jarvis from a terminal that can run npx…", "Tools → Connected packs" |
| `daemon/routes/settings.py:870` | "ollama pull llama3.2" |
| `connections/EndpointModelPicker.tsx:41-42, 65` | "Server type: OpenAI-compatible / Anthropic-compatible", always shown |
| `routes/assignments.py:95` and six other routes | "… not available on this daemon — restart it" |
| `app/terminals/page.tsx:1072, 1375`; `MoodOrb.tsx:205`; `app/sessions/page.tsx:288` | "Attaching to sessions…", "this terminal has no cwd", "· stream offline", "No orphaned worktrees to prune." |
| `NotificationBell.tsx:1286-1293` | A monospace short session id as the run link |
| `TurnReceipt.tsx:1047-1090, 1291` (expanded receipt) | "(custom) · fleet (default)", "context 43%". Acceptable as Level 2 detail. |

The user's install (v1.354.0) also still shows the Status-map words fixed in v1.356.0 ("Ready: anthropic, claude-cli, codex-cli, custom") until it updates.

### 0.7 Not built, by the owner's decision

- **G83, curated directory feed.** `connectors/catalog.py` is still the only source of Directory cards. Because there is now one catalog, a feed added later would only add rows to it.
- **G85, desktop-control pack.** Jarvis cannot click around other desktop apps. Settings now says so plainly (`BrowserPage.tsx:664`), so nothing over-promises.
- **G86, bundled local model.** A user with no API key and no signed-in CLI still has to install Ollama and download a starter model of about 2 GB before the first real answer. The one-press Ollama and the download button make that path shorter, but not five minutes.

### 0.8 Never proven live

- **Microsoft 365.** Nobody has signed in to Microsoft through it. The tests use a stand-in that lists the real server's tool names (`tests/test_microsoft_365_v13560.py`). After Connect, the card reads green "Connected" with 24 tools before any sign-in.
- **Another Jarvis (the task door).** Tested with two real app factories on one machine (`tests/test_another_jarvis_v13570.py`), never on two physical PCs. Between two packaged installs it cannot work without the command-line setup above.
- **`rest_call`.** Never run against a real CRM or any real service. It takes bearer keys only, and a service on this network needs `allow_internal`, which no card exposes.
- **GitHub's maintained remote server.** Never connected live. The 24-tool concern rests on GitHub's README.
- **Gemini API key.** There is no key on this PC, so Google has never answered through it.
- **Ollama.** The one-press use and the starter-model download are verified in code and tests only, because Ollama is not installed here.
- **Slack.** The new package, `@zencoderai/slack-mcp-server` 0.0.1, has never been run against Slack.
- **Waves 5 to 7 overall.** v1.355.0 to v1.357.0 were judged from code and tests. The running app is v1.354.0, and the newest published release is v1.356.0; v1.357.0 is not published yet.

### 0.9 Verdict

Iron Jarvis has gone from a strong engine with a developer-grade shell to a product most people could use day to day. The first audit's worst problems are fixed in the code:
- false "ok" and "Finished" over work that needs the user;
- silent unattended failures and a bell that forgot;
- green badges on packs that did not start;
- "daemon" in the banner, and raw tool ids in chat;
- an Emergency stop that did not stop.

It is not finished, and 83 items are still PARTIAL. The new problems are narrower, but users will meet them:
- a Schedules row that says "Done" over work that needs them;
- a web question that no longer searches the web when it names an app;
- GitHub probably missing its pull-request tools in chat;
- an anonymous failure line for a Claude Code timeout;
- two receipts for one helper run.

Fixing those five, and the desktop shell's "daemon" words, would be the most valuable next wave. After that, the biggest gap to a clean first five minutes is the one the owner deferred: a user with no key and no CLI still has to install Ollama and download a model.

---

## 0b. After wave 8 (v1.358.0)

Wave 8 ("close the re-audit") worked through section 0's lists in five lanes (8A-8E), a reviewer per lane, and an integrator. The scores in section 0 are the re-audit's and are not re-marked here. Each fix below is pinned by a test that was mutation-checked red without it. The running app was not re-checked live.

**All 14 regressions in 0.5 are fixed:**
- **Schedules row** — it leads with the outcome: Needs you, Part failed, Failed or Skipped, with the word itself coloured. A run that failed after its ask expired reads Failed. A parked workflow reads Needs you, and once answered it reads Done ("…finished after your answer", integrate). A refused goal reads Skipped.
- **Web research naming an app** — it reaches the web again. A search in the user's own data still goes to the app.
- **GitHub pull-request tools** — chat arms the 24 tools that fit the message. The integrator also capped an app that is both turned on and named at 24 in the stream lane (it read 44).
- **Anonymous failures** — the line names the provider ("Claude Code didn't answer in time"; Codex and OpenCode timeouts are typed too), and "claude-cli:" is never doubled.
- **Two receipts for one helper run** — there is now one.
- **"Used history search"** — the "What it did" lines use plain tool words.
- **"✓ Answered" over a cut-off reply** — it reads "Cut off after N s".
- **Directory built-ins** — they no longer sit under a Connect button.
- **"N connected"** — counts only working apps.
- **Raw summaries in the bell** — flattened to plain words.
- **rest_call** — reads "Called <service>".
- **The armed delete press** — red again.
- **README** — names the "Demo replies" strip.
- **Palette drift** — the bell and ConnectDoors use tone tokens.

**Gaps 0.4 listed as partly met:**
- **Met:** G01, G15, G30, G39, G76. G39's name-matching stays stream-lane only, by design.
- **Met in code:** G61. It has a setting that is off by default (Settings › Connections › "Let other computers on my network reach this Iron Jarvis"). The desktop applies it at each start, and it keeps the token rule. The register form now picks the task API itself. It is still untried on two physical PCs.
- **Met, apart from noted remainders:**
  - G19: the run log is in tool words. The mission's refusal line still names a built-in tool by its spaced id ("tried write file").
  - G41: built-in refusals say where to turn the tool on, and Pixio key errors name Settings › Connections. The Drive "Connect" button still appears only when the model calls app_connect.
- **Not built (owner's decision):** G83, G85, G86.

**Leaks from 0.6 that are closed:**
- the desktop's "daemon" wording;
- "the daemon log";
- write_file and app_connect refusals that showed raw ids;
- "Builder used history search";
- "Ran rest_call";
- the approval card's "run write_document" (write_document, excel_edit and generate_media now have words);
- the desktop ask toast's raw ids;
- the Pixio secret and PIXIO_API_KEY wording (media, the pixio_* tools and the portrait door);
- "MCP pack <name>" on Secrets;
- seven Settings labels, among them "Read a project's AGENTS.md and CLAUDE.md in chat" and "Autonomy (the pulse)" (the nav's "Sentinels", "Reflexes" and "Artifacts" are unchanged);
- the serve --host / allowlist sentence;
- the "… not available on this daemon" 503s (six routes; routes/assignments.py's other refusals still say "assignment");
- "+ › Connections" and the chat notes' "arm a document tool (the “+” menu…)", which pointed at the wrong button. The switches live in the chat's Tools menu.

**Also new:** the chat's apps section tells the model when Microsoft 365 still needs a sign-in, LM Studio and llama.cpp get a one-press offer, and Re-detect looks for them.

**Still open:**
- the model menu's "subscription/fleet" words and "Claude Code CLI" on Connections;
- "Session cancelled by the user.";
- the demo reply's opening line;
- the bell's short run id;
- the advanced paths listed in 0.6.
- (Closed by the re-review: a parked workflow cancelled while it waits now settles its row as stopped, and the chat's first read of Microsoft 365 after a restart waits for its sign-in check instead of saying "not signed in yet".)
- Microsoft 365, GitHub's server, the other-computer setup and LM Studio have never been tried live.

---

## 0c. After wave 9 (v1.359.0)

Wave 9 ("the last remainders") worked through 0b's open rows in three lanes (9A run words and Emergency stop, 9B the dashboard's last raw words, 9C the Connect door). Each lane had a reviewer, and an integrator joined them. The scores in section 0 are still the re-audit's. Every fix below is pinned by a test that went red when the fix was removed. The running app was not re-checked live.

**0b's remainders, closed:**
- **G19:** no line in a run's log reads a built-in by its spaced id, whether the call worked, failed, was refused or is waiting. Before, a refusal read "tried write file" and about 70 built-ins had no words at all ("used assign work"). Every registered built-in now has words in both twins (`agents/mission._RUN_TOOL_WORDS` and `lib/runWords` AGENT_TOOLS), and a test fails on a new one that has none. A connected app's tool reads as the chat footnote names it ("Gmail › search messages"), both in the run log and in the run card's "What it did".
- **G41:** naming an app that is not connected, for your own data in it, always puts its Connect button under the reply, whatever the model does. Research about an app ("how much does Dropbox cost?") offers nothing. It is one door at the same address as app_connect's own door (the Directory). "I have no notion what to write" and "there is slack in the schedule" no longer offer Notion or Slack.
- **G13:** Emergency stop now also stops a scheduled workflow that is waiting on a question, including runs parked before the person mark existed. Its Schedules row reads "Emergency stop was turned on, so your workflow … was stopped before you answered." A workflow a person ran keeps waiting. The kill route's "Stopped N running tasks" count is now always its own: the settings watcher no longer races it.

**0b "Still open", closed:**
- **The model menu's "subscription" / "fleet":** the menu now reads "Your Claude plan", "Your ChatGPT plan" and "Your model server". The integrator found six more model lists printing "claude-cli · subscription": Settings' default model, an agent's preferred model, a terminal's assist model, templates, a project's default and the New task form. All seven lists now share one rule (`lib/onboarding.modelPickWords`), with the raw id in the hover text. The Try-again menu and the composer's filter follow the same words.
- **"Claude Code CLI" on Connections:** the rows read Claude Code, Codex and OpenCode.
- **"Session cancelled by the user.":** a stopped run says "The agent was stopped before it finished." in its summary, its chat line and the reply home. Runs stored before this release read the same sentence on the run page.
- **The demo reply's opening:** it now opens "This is the demo model: a scripted demo, not real answers." and claims RESULT.md only when the save really happened. The demo is named once in the chat, on the task door and in the browser sidebar.
- **The bell's short run id:** the ask row links the run by its task, and the id is only in the hover text.
- **A run's failure advice:** inside a run it says "Run it again, or choose another model in Settings.", including the router's technical text. The Schedules row of a run that raised reads the run's own sentence, not raw CLI text. Chat keeps "Press Retry, or pick another model for this chat." word for word.

**What honestly remains:**
- **The approval card's words for rarer built-ins:** `lib/toolWords` words about 15 built-ins. A card asking for one of the others ("assign work", "schedule delete") still says "run <its id>", with the id kept on the card because a grant is keyed on it. The run log and the desktop toast's twins are unchanged by this wave. Wording them means `lib/toolWords` WORDS, `mission.ASK_WORDS`, `desktop/main.js` TOOL_WORDS and their fixture, kept equal.
- **Advanced paths from 0.6:**
  - the REST service's "Config (JSON)" box;
  - the agent form's "http-task (task API)";
  - OAuth client steps;
  - "ollama pull llama3.2";
  - "Tools → Connected packs";
  - "Pending approval id=<id>";
  - "Attaching to sessions…";
  - the nav's "Sentinels", "Reflexes" and "Artifacts";
  - `routes/assignments.py`'s "no assignment …".
- **Not pinned:** Emergency stop's compare-and-set on a waiting workflow. Only a race between its read and its update would show it, and the read already filters.
- **Never tried live:** Microsoft 365, GitHub's server, two physical PCs on the task door and LAN setting (the Windows Firewall prompt too), LM Studio/llama.cpp, a real CRM through rest_call, a Gemini key and Ollama on this PC.
- **Owner decisions, unchanged:** G83 (curated directory feed), G85 (desktop-control pack) and G86 (bundled local model). A user with no key and no signed-in CLI still has to install Ollama and download a model before the first real answer.

---

## 1. Overall product-readiness score: **55 / 100**

The goal: a completely new user can install Jarvis, understand it, connect their existing tools and AI, get useful work done, and understand exactly what happened, without learning how it is built.

Two parts are already product grade:
- **The engine** (stability, recovery, honesty in the ledger).
- **The main chat surface** (live work line, "answered by", step details, diffs with Undo, approval cards with previews).

Everything around chat is still developer grade:
- Background work, schedules, missions, connections, import, settings and error text each invent their own words.
- Some of these surfaces contradict the ledger: a schedule shows "✓ ok" over a run that needs the user, and a mission says "Finished" over failed parts.
- Several leak the machinery: "Daemon offline", `mcp__gmail__search_messages`, `claude-cli`, "stdio".

Almost every gap is presentation over data the app already stores. That is why the score is mid-range rather than low, and why the plan below needs no new subsystem.

## 2. Category scores

| Category | Score | Why |
|---|---|---|
| Action clarity | 60 | Chat answers every question. Schedules, missions, the helper-agent card and the browser sidebar answer some questions wrongly (✓ ok, "Finished", red "Task failed" for a Stop, "4 steps"). |
| Execution visibility | 62 | Excellent live work line, helper-agent row and plan checklist. Background work is a pulsing orb plus counts. Bell rows vanish when the window closes to the tray. |
| Connections | 42 | Many doors and two catalogs. Five Directory packages are deprecated on npm. A green "Connected" shows for packs that did not start. An app reaches chat only through a hidden per-chat switch. No Outlook/M365, no Jarvis-to-Jarvis link. |
| Import/discovery | 45 | The parser and scanner are strong and found 6 servers on this PC. They sit four levels deep under Permissions & ledger, read as configuration, and miss current VS Code files. |
| First-run experience | 52 | Good for someone already signed in to Claude Code or Codex. Otherwise: no SmartScreen guidance on the release page, a demo reply that contradicts itself, Ollama detected but refused, and a confirmation that vanishes. |
| Error/recovery UX | 60 | The backend recovers well and the chat's error line is good. Elsewhere errors are raw strings, exception class names or terminal instructions. Recovery is mostly invisible. Approvals can be abandoned silently. |
| Consistency | 40 | No shared status words. Four approval layouts, and "Always" means two different things. The Directory, Tools, the chat card and the mission screen each show the same thing differently. |
| Discoverability | 50 | Everything, Ctrl K and Help exist. Everything is a 30-item flat list. "What can you do?" does not consult the Guide. Phone chat and self-care are hidden. |
| Beginner friendliness | 45 | About 65 strings say "daemon". The model menu shows raw provider ids. Settings asks for a provider id and a free-text model id. |
| Trust | 55 | Strong inside chat: approval previews, Local only, Undo, receipts. The rest is weak: Emergency stop over-promises, cloud models are told they are local, reading any file without asking is never disclosed, approval cards never say whether an action can be undone, and secrets in pack arguments are shown back. |
| Stability | 85 | 11 of 14 stability items PASS. Work survives navigation, reload, restart and updates. Processes are cleaned up and resources bounded. The remaining gaps are about communication, not reliability. |
| Marketplace/capability readiness | 58 | `list_connectors`, `add_mcp_pack`, load records, the import scan and `app_connect` are a real base. Missing: one catalog, one add/remove door, requirements and permissions as data, on/off. |

## 3. PASS / PARTIAL / FAIL table

| Section | Items | PASS | PARTIAL | FAIL |
|---|---|---|---|---|
| A. Actions self-evident (incl. 12 surfaces) | 33 | 3 | 30 | 0 |
| B. Execution receipt | 24 | 10 | 14 | 0 |
| C. Connections | 21 | 2 | 17 | 2 |
| D. Import | 16 | 1 | 15 | 0 |
| E. First run | 11 | 1 | 10 | 0 |
| F. Natural-language discovery | 6 | 0 | 6 | 0 |
| G. Error & recovery | 10 | 4 | 6 | 0 |
| H. Consistency | 15 | 2 | 13 | 0 |
| I. Engine room | 14 | 2 | 11 | 1 |
| J. Discoverability | 5 | 0 | 5 | 0 |
| K. Trust | 11 | 1 | 10 | 0 |
| L. "Did it work?" | 14 | 2 | 12 | 0 |
| M. "What does this mean?" | 8 | 0 | 8 | 0 |
| N. Stability | 14 | 11 | 3 | 0 |
| O. Marketplace readiness | 11 | 2 | 9 | 0 |
| **Total** | **213** | **41** | **169** | **3** |

### A. Actions should be self-evident
| Item | Mark | Main reason | Gap |
|---|---|---|---|
| Did Jarvis do what I asked? | PARTIAL | Chat is honest. Schedules "✓ ok" and the mission "Finished" contradict the ledger. | G01, G09 |
| Is Jarvis working? | PASS | Live work line, helper row, orb, pane chip | — |
| What is it doing now? | PARTIAL | Background work is shown only as an orb hover and counts | G26 |
| What did it just do? | PARTIAL | A finished agent run loses its steps; raw tool ids on the card | G18, G19 |
| Did it succeed? | PARTIAL | ✓ ok on runs that fell short; a check icon beside failures; sidebar "4 steps" | G01, G07, G53 |
| Partially succeeded? | PARTIAL | Missions and schedules drop "part failed" | G01, G09 |
| Did it fail? | PARTIAL | Unattended failures are silent; the bell forgets; the Status flag can never fire | G02, G03, G04 |
| Why did it fail? | PARTIAL | Class names and raw tool output outside chat | G01, G19, G28 |
| Did it recover? | PARTIAL | Context refit is silent; agent failover is not on the card | G55, G18, G19 |
| Why did it stop? | PARTIAL | A user Stop shows as a red "Task failed" | G16 |
| Does it need me? | PARTIAL | A chat turn's ask never reaches the bell or desktop; 1-hour orphan stop | G05, G03 |
| Which agent/harness? | PARTIAL | `agent_name` is never sent; the harness shows only in a hover | G18, G19, G20, G23 |
| Which model/provider? | PARTIAL | The agent card names none; missions show raw ids | G18, G19, G23 |
| Which machine/node? | PARTIAL | The node label is hover-only; no "where" word | G21 |
| Which tools/connections? | PARTIAL | "Ran mcp__gmail__search_messages" | G22 |
| Files created/modified | PASS | Receipt files, diffs, Undo | — |
| Where did output go? | PARTIAL | Folder "Open" returns a 404; the folder is not named on the card | G11, G19 |
| How long did it take? | PARTIAL | "656.8s" vs "10 min 57 s"; no duration on several surfaces | G19, G20, G23, G53, G63 |
| Can I inspect it? | PASS | Step detail, Time-travel, Activity export | — |
| Desired summary-then-expand UX | PARTIAL | Close in chat; missing outcome mark, "where", app names, fix link | G07, G21, G22, G41 |
| Chat | PARTIAL | Three reply shapes in one transcript | G19, G62 |
| Build | PARTIAL | Pane receipt lacks chat's words; files listed three times | G51 |
| Agents | PARTIAL | One run shown four ways | G17, G19, G20 |
| Missions | PARTIAL | Its own card, raw ids, "Finished" over failures | G09, G23 |
| Tools | PARTIAL | Names do not match chat's rows | G22 |
| Browser/computer use | PARTIAL | "4 steps"; approving resumes nothing | G53, G54 |
| MCP | PARTIAL | Raw ids in work rows | G22 |
| Remote machines | PARTIAL | No receipt, no pending state | G62, G21 |
| Background work | PARTIAL | Each subsystem decides for itself; rows vanish | G03, G04 |
| Scheduled work | PARTIAL | ✓ ok over failures; dead Status flag | G01, G02, G03 |
| Failures/recovery | PARTIAL | Every surface has its own failure words | G16, G17, G28, G29 |
| Approval requests | PARTIAL | Four layouts; two meanings of "Always" | G24, G05 |
| Common abstraction | PARTIAL | The backend spine is shared; each frontend invents its own | G17–G20 |

### B. Standardized execution receipt
| Item | Mark | Gap |
|---|---|---|
| Universal receipt | PARTIAL: two families; `session_result` lacks model, agent, where and actions | G18 |
| Objective, Current state, Start, Completion | PASS | — |
| Duration | PARTIAL | G19, G20, G63 |
| Agent | PARTIAL | G18, G20 |
| Harness | PARTIAL | G19, G23 |
| Model | PARTIAL | G18, G19, G23 |
| Provider | PARTIAL | G18, G19 |
| Machine/node | PARTIAL | G14, G21 |
| Tools used | PASS | — |
| Important actions | PARTIAL | G18, G19 |
| Files affected, Result, Failure | PASS | — |
| Recovery attempt | PARTIAL: refit not recorded | G55, G18 |
| Approval/intervention | PARTIAL: chat replies keep no record of approvals given | G64 |
| Child work | PASS | — |
| Final outcome | PARTIAL | G01, G09 |
| Level 1 human result | PARTIAL | G07, G17 |
| Level 2 timeline | PARTIAL | G18, G19 |
| Level 3 forensic | PASS | — |
| Does the data exist? | PARTIAL: mostly yes, so mostly UX. Data gaps: refit, approvals given, a stored "where" word. Build-pane CLI work has no ledger, by design. | G55, G64, G14 |

### C. Connections
| Item | Mark | Gap |
|---|---|---|
| One understandable place | PARTIAL: five or more homes, two catalogs, many names | G42 |
| GitHub | PARTIAL: deprecated package; needs the hidden + toggle | G15, G39, G44 |
| Gmail | PARTIAL: outside OAuth setup; green "Connected" while failing | G45, G08 |
| Google Drive | PARTIAL: developer console after the press; makes nothing searchable | G45 |
| Microsoft 365 / Outlook | **FAIL** | G60 |
| Slack | PARTIAL: deprecated package; two unrelated Slacks | G43, G45 |
| Notion | PARTIAL: best path, still the toggle | G15, G45 |
| Browser | PARTIAL: four routes including deprecated Puppeteer | G43 |
| Local files | PARTIAL: built in, but surfaces say "connect" | G42 |
| Computer control | PARTIAL: "(and, later, the desktop)" over-promises | G71 |
| Claude | PARTIAL: Accounts offers only terminal text | G33 |
| Codex | PARTIAL: same | G33 |
| OpenAI | PASS | — |
| Gemini | PARTIAL: no API-key path | G46 |
| Ollama | PARTIAL: detected but refused; "appears automatically" is untrue | G34, G73 |
| LM Studio | PARTIAL: not detected, no preset | G72 |
| OpenRouter | PASS | — |
| MCP | PARTIAL: the word appears on primary surfaces | G42, G48, G74 |
| Custom API/tool | PARTIAL: REST hookups are a dead end | G48, G84 |
| Remote Jarvis node | **FAIL** | G61 |
| Local models | PARTIAL: "Not connected" while it answers; two add forms | G47 |

### D. Import what the user already has
| Item | Mark | Gap |
|---|---|---|
| Claude Code | PARTIAL | G42, G50 |
| Claude Desktop | PARTIAL | G42 |
| Codex | PARTIAL | G42 |
| Cursor | PARTIAL | G50 |
| VS Code | PARTIAL: misses the current mcp.json | G50 |
| Existing MCP config | PARTIAL | G42 |
| Local model servers | PARTIAL | G34, G72 |
| Other AI tooling | PARTIAL | G50 |
| Discovery automatic? | PARTIAL: the scan runs only in a buried tab | G42 |
| Import understandable? | PARTIAL | G49 |
| Secrets safe? | PARTIAL: argument and URL secrets shown raw | G12 |
| Explains what was found? | PARTIAL | G49, G50 |
| Explains what was imported? | PARTIAL | G49 |
| Shows whether it works? | PASS | — |
| Failed import repairable? | PARTIAL | G49, G69 |
| Feels like importing? | PARTIAL | G49 |

### E. Five-minute first run
| Item | Mark | Gap |
|---|---|---|
| Installation understandable | PARTIAL: no guidance on the release page | G37 |
| Launches | PASS | — |
| Checks environment | PARTIAL: results only on Everything › Status | G36 |
| Problems in normal language | PARTIAL | G30, G33, G10 |
| Detects available AI | PARTIAL: Ollama | G34 |
| Connect/select AI without architecture | PARTIAL | G35, G46, G73 |
| Optional connections easy | PARTIAL | G36, G42 |
| Knows setup is complete | PARTIAL | G35 |
| Knows what to do next | PARTIAL | G38 |
| Useful work in 5 minutes | PARTIAL | G10, G86 |
| No docs needed | PARTIAL | G38, G37 |

### F. Natural-language capability discovery
All six are PARTIAL:
- Determine it exists: G39.
- Available: G15, G39.
- Configured: G40, G45.
- Tell what is missing: G40, G41.
- Lead to the solution: G41, G40.
- Resume the task: G15.

### G. Error and recovery UX
- **PASS:** error appears where the user was; identifies what failed; safe automatic recovery; work can continue after repair.
- **PARTIAL:**
  - Plain language: G28, G30, G32.
  - Technical detail optional: G28, G29.
  - What was NOT affected: G29, G30, G32.
  - User sees recovery: G31, G55.
  - Clear action: G29, G32, G33, G51.
  - Never silently abandons: G01, G03, G05.

### H. Consistency
- **PASS:** empty states; loading states.
- **PARTIAL:**
  - Terminology: G17.
  - Success: G01, G08, G19.
  - Failure: G08, G16, G59.
  - Approval: G24.
  - Connection: G08, G44.
  - Progress: G20.
  - Expandable detail: G20.
  - Buttons: G24, G59, G74, G77.
  - Navigation: G20, G67.
  - Similar actions look similar: G24, G80.
  - Destructive actions: G68.
  - Background disappears: G02, G03.
  - Reopening: G04.

### I. Hide the engine room
- **PASS:** inference server; harness.
- **FAIL:** daemon (about 65 strings, on the most common paths): G30, G59.
- **PARTIAL:**
  - MCP: G42, G48, G74.
  - transport: G49, G74.
  - SSE: G49, G74.
  - stdio: G74.
  - provider internals: G57.
  - runtime: G74.
  - context details: G75.
  - Tailscale: G58.
  - raw configuration: G57, G72, G74.
  - raw JSON: G48, G74.
  - environment variables: G74.

### J. Discoverability
All five are PARTIAL:
- Can be found: G56, G66.
- Purpose obvious: G66.
- Obvious start: G56.
- Contextual surfacing: G32, G38, G41.
- Not overloaded: G66.

### K. Trust
- **PASS:** inspect its work.
- **PARTIAL:**
  - What it is doing: G26.
  - Why: G24.
  - Where: G19.
  - Access: G27.
  - What needs approval: G24, G65.
  - What happened while away: G03, G04.
  - Can I stop it: G13.
  - Undo: G25.
  - Local vs cloud: G14, G21.
  - Which machine: G20, G21.

### L. "Did it work?" test
- **PASS:** chat request; failure/retry.
- **PARTIAL:**
  - Coding task: G52.
  - Tool call: G07, G22.
  - Pack installation: G08.
  - Connection setup: G08, G47.
  - Browser task: G22, G53.
  - Computer use: G54.
  - Agent delegation: G16, G19.
  - Mission: G09.
  - Remote task: G62.
  - Scheduled task: G01–G04.
  - File creation: G06, G07.
  - Approval flow: G24.

### M. "What does this mean?"
All eight are PARTIAL:
- Technical terms: G75, G77.
- Ambiguous labels: G26, G67, G75.
- Ambiguous icons: G26, G75.
- Statuses without consequences: G21, G77.
- Controls with unclear effects: G24, G74, G77.
- Duplicate concepts: G76.
- Multiple names for one thing: G76.
- Architecture exposed: G30, G42, G57.

### N. Stability
- **PASS (11):** navigation; reload/close; restart; agent state; duplicate prevention; tool isolation; remote isolation; long work manageable; resources bounded; processes cleaned; updates safe.
- **PARTIAL:**
  - Background visible after returning: G04.
  - Failed work has an understandable state: G01, G02, G20.
  - Recovery understandable: G20, G31.

### O. Marketplace readiness
- **PASS:** exposes actions/tools; can be installed/imported.
- **PARTIAL:**
  - Common representation: G43, G82.
  - Consistent mechanisms: G08, G42, G44.
  - Self-description: G42.
  - Health: G47.
  - Setup requirements: G45.
  - Permissions: G70.
  - Enable/disable: G78.
  - Repair: G69.
  - Intent: G39.

**How a curated directory fits later without re-architecture:**
1. `connectors/service.list_connectors` already merges the catalog, user packs and memory sources into one honest row shape.
2. `add_mcp_pack` / `remove_mcp_pack` is already the vaulting, live-loading door.
3. Load records already give reason, fix and output.
4. The import scan and `app_connect` already cover "bring" and "connect from chat".

Once G43, G44, G45, G70 and G78 land, the catalog rows carry needs, permissions and on/off as data. A community feed then only has to produce more catalog rows (G83).

## 4. Top 10 gaps (ranked by user impact × frequency × confusion/risk)

1. **Background results are wrong or silent (G01, G02, G03, G04).**
   - A scheduled run that needs the user, or partly failed, shows a green "✓ ok" and the notice says "done". This happened on 3 of the last 4 weekly memory reviews on this install.
   - A failed unattended run rings nothing.
   - Bell rows vanish when the window closes to the tray.
   - The Status map's "schedules failed" flag can never fire, because it compares `'failed'` while the scheduler writes `'error'`.
   - This is the core "did it work?" failure for anyone who automates. P0, Small/Medium.
2. **A connected app does not reach the chat that asked for it (G15, then G39).**
   - "Connect my GitHub" ends at "saved securely". The next message has no GitHub tools until the user finds "+ › Connections" for that chat.
   - The model is never told what is connected.
   - "Search my Google Drive" runs a web search instead.
   - P0/P1, Medium.
3. **Success and failure signals that tell the wrong story (G06, G07, G08, G47).**
   - A real resized image gets "nothing was written to disk".
   - "✓ read 8 web pages, 7 failed".
   - A green "Connected" sits above "Didn't start", and a green "added" for a pack with 0 tools.
   - The user's own model server reads "Not connected" while it answers every chat.
   - P0, Small.
4. **The first five minutes (G10, G37, G34, G35, G36).**
   - The release page gives no SmartScreen guidance.
   - The demo reply says "Done. Wrote RESULT.md", then "nothing was written, arm a document tool".
   - A running Ollama is listed, then refused.
   - The "you're set up" confirmation disappears.
   - P0/P1, Small/Medium.
5. **Engine words on the most common paths (G30, G28, G57, G59).**
   - "Daemon offline." on every page and inside chat bubbles.
   - Raw provider ids in the model menu.
   - "anthropic runs through claude-cli", "local_primary_policy", "open a terminal and run /login".
   - P1, Medium.
6. **A chat waiting for your OK is never announced and is stopped after an hour (G05).** The work is silently abandoned. P0, Medium.
7. **Emergency stop over-promises (G13).**
   - "Immediately blocks every self-initiated action", reached by typing "stop everything" in Ctrl K.
   - In reality only the motivation loop reads it. Schedules, goals, reflexes and phone jobs keep running.
   - P0, Medium.
8. **No shared vocabulary for status and approvals (G16, G17, G24, G09).**
   - A Stop reads red "Task failed".
   - A mission with failures says "Finished".
   - Four approval layouts, and "Always allow" means "every tab" in one place and "this exact call" in another.
   - P1, Small/Medium.
9. **Connections are fragmented and partly stale (G42, G43, G44, G45, G49).**
   - Import is buried four levels deep.
   - Two catalogs disagree.
   - Five deprecated packages are offered.
   - Requirements show only after the press.
   - P1, Medium.
10. **Trust disclosures (G14, G21, G27, G25, G12).**
    - Cloud models are told they run locally.
    - Replies never say whether they ran locally or in the cloud.
    - Reading any file without asking is never disclosed.
    - Approval cards never say "can't be undone".
    - Secrets in pack arguments are shown on the Tools row, which contradicts HANDBOOK.md:2084.
    - P0/P1, Small/Medium.

## 5. Engine room leaks

| Where | Today | Say instead | Gap |
|---|---|---|---|
| DaemonBanner.tsx:256, 343, 402 (every page) | "Daemon offline." / "Daemon rejected your token." / "A request to the daemon failed." | "Iron Jarvis isn't responding." / "This window isn't signed in to Iron Jarvis." / "Part of this page didn't load." | G30 |
| useChatStream.ts:827-840 (chat bubble) | "Nothing arrived from the daemon for 90 s…" | "Nothing came back from Iron Jarvis for 90 s…" | G30 |
| PreflightNote.tsx:67; StatusOverview.tsx:152; ui.tsx:495; api.ts:193 | "couldn't reach the daemon", "Daemon offline", "daemon offline" | "Iron Jarvis isn't responding" | G30 |
| desktop/loading.html:94 | "Booting the daemon and dashboard" | "Getting Iron Jarvis ready" | G30 |
| MaintenanceCard / LegacySettingsForm; SettingsHome:136,142 | "Restart daemon", "reach the daemon" | "Restart Iron Jarvis" | G59 |
| projects/[id], ProjectSurfaces, SavedWorkflows, ProjectSchedules, Creative | "the daemon returned an error (HTTP 500)" / "Daemon offline — could not upload." | "The board couldn't load. Try again." (code in a title) | G59 |
| connectors/service.py:204, 302 | "saved — restart the daemon…" | "Saved, but it didn't start. Press Retry." | G08 |
| router.py:897-925 | "anthropic runs through claude-cli…", "local_primary_policy=refuse… Set local_primary_policy to failover" | "Claude Code isn't signed in." / "Iron Jarvis won't switch to a different AI without asking." | G28 |
| ConnectDoors.tsx:478 above its own Sign in button; ConnectionsPage CLI rows | "Open a terminal, run `claude`, then `/login`…", "install / log in via its CLI" | A Sign in button and a vendor link | G33 |
| adapters/base.py:94-98 | "openrouter API error 401: No auth credentials found" | "OpenRouter refused your key. Update it in Connections." | G28, G29 |
| orchestrator.py:1809; platform.py:2108 | "Session failed: ProviderError: …", "RuntimeError: session ended failed: …" | "This run stopped: <reason>" / "The run failed: <reason>" | G28, G01 |
| Schedules last_detail (live) | "# Final Report ## What happened…" | First plain sentence | G01 |
| Bell and Status loop rows | "Background task slack_socket has been failing for 12 min" | "Slack messages aren't arriving · Open" | G32 |
| Composer model menu (live /models) | anthropic / claude-cli / codex-cli / custom / opencode-cli; rows "subscription", "fleet" | Claude / Claude Code / Codex / Your model server / OpenCode | G57 |
| Settings › Models | "Default provider" (raw ids); "Default model" text box "claude-opus-4-8" | The composer's model picker | G57 |
| Mission receipt, AgentCards | "Answered by custom · fleet" | "Answered by <model> on <endpoint>" | G23 |
| Work rows | "Ran mcp__gmail__search_messages", "Ran browser_click" | "gmail: search messages", "Used your browser" | G22 |
| RunResultCard | "history_search ×83", "memory_read — missing required: layer…", "656.8s" | "Searched your chats 83 times", "One memory lookup failed" (raw text folded), "10 min 57 s" | G19 |
| Bell ask row | "It wants to run write_file" (monospace) | "It wants to save a file · for: <task>" | G24 |
| Session page | "Session / session_7b32f746bc3a", raw tables, JSON live events | The task as the title; plain step words; "Full record" fold | G20 |
| Directory cards | "MCP / OAUTH / API KEY" tags; "Custom · Your own MCP server." | No tag; "Added by you" + what its tools do | G42 |
| ConnectionsPage; ToolsPage blurb | "Extensions (MCP)", "runs as its own program (MCP)" | "Add a tool"; "Some need a free helper program (Node.js)" | G48, G42 |
| AddPack | "Paste a config", "Streamable HTTP / SSE", "Bearer token", raw launch commands | "Bring your tools from Claude, Codex, Cursor…"; transport auto-detected under More options; command under Details | G49 |
| Tools rows, Enable dialog, EditPack, FilterBar, Build Launch menu, ConfinementChip | "N env", "<folder-path>", "Passed … as an environment variable", "No runtime needed", "Jarvis capabilities over stdio", "Shell ran unconfined (Docker unavailable)" | "N settings", "Folder", "Kept encrypted on this PC", "Works without installing anything", "Jarvis tools ready", "Ran without the safety box" | G74 |
| Compaction offer and chip; model rows | "context window", "Compact now", "at the context ceiling · written by <id>", "1M" | "This chat is getting long. Summarize the start?", "long docs" | G75 |
| Chat tools menu | raw tool ids, "armed / disarm" tooltips | Tool words; Turn on / Turn off | G75 |
| RestHookups; EnvelopeCard | "Direct REST hookups", "Config (JSON)", "guided JSON", "JSON adherence 0.93" | An advanced fold; "follows formats reliably" | G48, G74 |
| media/registry.py:23-27 | "add a Pixio key as the secret 'pixio' … or set PIXIO_API_KEY" | The secure key card; "Settings › Connections › Accounts & keys" | G40 |
| chat_turn.refusal_footnotes | "mcp__github__create_issue could not run (not turned on for this chat)" | "GitHub › create issue isn't on for this chat. Turn GitHub on in + › Connections." | G41 |
| Demo mode | "Mock answer. No real model ran." / "offline mock" / "arm a document tool" | One demo name; "Choose a model above" | G10, G76 |
| Help (phone) | "Tailscale … 100.x.y.z:8788 … no daemon settings to change", IRONJARVIS_HOST_ALLOWLIST | Two-way messaging first; recipe marked advanced with its real requirements | G58 |
| fs_policy refusal | "path is outside IRONJARVIS_FS_ALLOWLIST" | "That folder isn't one Jarvis may read." (and a setting) | G81 |
| System map stars (live) | "Ready: anthropic, claude-cli, codex-cli, custom. Answers come from custom."; "Chrome" hard-coded | providerDisplay words; the paired browser's name | G77 |
| Onboarding local door | "copy this into a terminal: ollama pull llama3.2" | "Download a starter model" button | G73 |
| Accounts OAuth providers | "save its client id as <provider>_oauth_client_id in Secrets" | Client ID / Client secret fields (Gemini also gets an API key) | G46 |
| Settings rows (not folded) | "Read a project's AGENTS.md and CLAUDE.md", "Iron-Proxy (shared accounts)", "Autonomy (the pulse)", "Let every app (MCP) tool run…" | "Follow the instruction files in a project's folder", "Share several Claude/Codex accounts" … | G74, G77 |
| AppSidebar footer | "Running · v1.350.2" (means "the app is up") | "Ready" | G26 |
| SetupCard remote agent | "http-task (task API) / openai-chat / openai-responses" | Probe and pick automatically; "Agent on another computer" (unscheduled polish) | — |

## 6. "Did it work?" failures

| Workflow | Why a first-time user would ask | Fix |
|---|---|---|
| Scheduled / queued task | "✓ ok" and "done" for runs that need the user or partly failed. Run now says "Ran ✓". Nothing in the bell. The toast is lost in the tray. The Status flag is dead. | G01, G02, G03 |
| Anything that finished while the window was closed | Bell rows existed only in the closed window's memory | G04 |
| A chat turn you left on "Allow this?" | Never announced; stopped after 1 hour unwatched | G05 |
| Tool call | A check beside "7 failed"; "read 8 pages" when 1 was read; raw pack ids | G07, G22 |
| File creation/modification | A real image/PDF/edit is accused of being fake. A failed edit counts as "made 1 file". Claims of a change are never checked. | G06, G07 |
| Pack install / Directory connect | Green "Connected"/"added" over a pack that did not start; "restart the daemon" | G08 |
| Custom endpoint / own model server | Save is always green; the card says "Not connected" while it answers | G47 |
| Connect an app from chat | "Saved securely"; no load result, no resume, no tools next turn | G15 |
| Image or video with nothing connected | You approve, then learn nothing is connected | G40 |
| First message before choosing a model | "Done. Wrote RESULT.md" + "nothing was written" + "Mock answer" | G10 |
| Choosing a model / pasting a key | The confirmation vanishes and names the wrong thing; the key door does not say the next step | G35 |
| Picking a detected Ollama model | Listed as available, then refused | G34 |
| Agent hand-off you stopped | Red "Task failed"; the settled row has no outcome | G16, G19 |
| Mission with failed parts | Says "Finished" while the bell says "part of it failed" | G09 |
| Remote-machine task | One "will report back" line, no pending state; a remote pick silently ran on this PC | G62, G19 |
| Coding task in Build | A green "finished" check means only "went quiet"; "needs you" is invisible elsewhere | G52 |
| Browser sidebar task | Ends as "4 steps" | G53 |
| Agent-browser sensitive step | "It pauses here and waits", but approving resumes nothing | G54 |
| Approving from the bell | The row vanishes with no confirmation | G24 |
| Long chat | Older messages dropped to fit; not said | G55 |
| Chat work-folder "Open" | 404 "no such file" | G11 |
| REST hookup | Test says healthy; nothing can use it | G48 |
| Emergency stop | "All autonomy halted" while schedules run | G13 |
| Import from other apps | "26 tools ready", but not usable in chat without the toggle | G49, G15 |
| Directory Google Drive | "Ready to use" while nothing searches Drive | G45 |
| Help: dashboard on your phone | The recipe very likely cannot work on the packaged app | G58 |

## 7. Existing infrastructure to reuse

**Outcomes and events**
- **Session.outcome + `derive_outcome`, and `orchestrator._completion_payload`.** Every `session.completed` event already carries origin and outcome. One bell rule (G03) and the schedule recorder (G01) only need to read it.
- **The persisted EventRecord + GET /audit** (`observability.timeline`). This is the bell's memory after a reopen (G04); it needs only a multi-type filter and a type field.
- **`/ui/system-map` and `/ui/status-line`.** Fix the "failed"/"error" compare and add one count, rather than building a new reader (G02).

**Receipts and words**
- **`session_result` (`agents/outcome.py`) and the Session row.** The Session row already holds the served provider/model once a run ends (orchestrator.py:1271). Grow it (G18) rather than adding a receipt service.
- **`agents/mission.py`** `tool_line`, `_QUIET_TOOLS`, the activity loop and `_route_note`: a ready plain-words timeline and recovery note once lifted out (G18).
- **Word tables:**
  - `lib/stepWords` + `lib/workTarget` is the one step vocabulary; extend it to app and browser names (G22).
  - `lib/toolWords` covers asks.
  - `secondsText`, `friendlyModelName`, `answeredModel` and `onboarding.providerDisplay` cover durations and names.
  - `SessionStatusBadge.outcomeLabel` and `lib/mission.statusWord` merge into one status table (G17).
- **The `AgentRunSettled` children slot + `settleHandOff`.** A ready place for the finished run's actions (G19).

**Approvals, trust and running work**
- **`ApprovalCard` (+ `ApprovalPreview`).** One card already serves chat, Build and the session page; export its labels for the bell and missions (G24). `Tool.reversibility` gives the undo line (G25).
- **`egress.stays_on_this_network` + `route_flags`** (already on both lanes' route objects) and `onboarding` WHERE words: the "where" word and an honest system prompt (G14, G21).
- **`TURNS.thread_states()`, `durable_turns.TurnLog.watched()`, `_running_for`.** These say which chats are running or waiting and whether anyone is watching (G05, G26).
- **`orchestrator.create_session` + `runtime.ATTENDED_ORIGINS` + `cancel_session`.** The one place to make Emergency stop real (G13).

**Connections and import**
- **`connectors/service.list_connectors`, `routes/agents.add_mcp_pack` / `remove_mcp_pack`, `launcher_ready`, load records.** The basis of one Connections home and one add door (G42–G45).
- **`mcp/import_sources.scan` (`KNOWN_SOURCES`) and `import_config.parse_any`.** Discovery is built; surface it and add files (G42, G50). `detections.redact.mask` handles masking (G12).
- **`settings/records.AppConnectTool`, `CredentialCard` (onSettle), `toggleConnector`, `send()`.** The connect-and-continue path (G15).
- **`daemon/doors.py` + `DoorsStrip`.** Add a failure-door sibling for fix buttons (G41).
- **`tools/autoselect` Guide rule + guide tools; `tools/selfcare` readers.** Widen one regex and link failures into chat (G38, G32).

**First run**
- **`ConnectDoors` `startSignIn` / `connectOllama`, `AnswerPress`, `POST /onboarding/use-model`, `terminals/ai_clis` + POST /terminals/launch.** Sign-in, one-press and model-download doors without a terminal (G33–G35, G73).
- **`OnboardingWelcome` PressOutcome latch, `checklist.set_up_voice` (optional step pattern), `FirstRunStrip.chatAskHref`.** The first-run patterns (G35, G36, G32).

**Shell and navigation**
- **`lib/surfaces.ts` + `lib/nav.ts`.** One manifest for sidebar, Everything and Ctrl K: tiering and blurbs in one place (G65, G66).
- **`InterruptedJobActions` + `parseInterrupted`.** Continue with resume on the session page (G20).
- **`useDaemon().epoch`.** The "back online" edge (G31).
- **`desktop/main.js` `installAskWatcher`.** The main-process watcher for asks and blocked panes (G05, G52).

## 8. Recommended plan

**P0: prevents trust, usability or stability problems (15).**
- **Outcomes:** G01 schedule/queued outcome; G02 Status/home flag; G03 bell rule for unattended work; G04 bell memory.
- **Silent abandonment and false signals:** G05 chat-waiting announcement; G06 honesty-note false accusation; G07 work-line failure mark; G08 green-only-when-working; G09 mission headline.
- **First run:** G10 honest demo reply.
- **Broken button:** G11 folder Open.
- **Trust:** G12 pack-argument secrets; G13 Emergency stop; G14 no "you run locally" for cloud models.
- **The core F scenario:** G15 connect-in-chat works and continues.

**P1: required for an excellent general-user experience (44).**
- **One vocabulary and receipt:** G16–G23.
- **Approvals and trust:** G24–G27.
- **Plain words and recovery:** G28–G32, G59.
- **First run and models:** G33–G38, G46, G47, G57.
- **Chat knows what you can connect:** G39–G41, G55.
- **Connections and import:** G42–G45, G48–G50.
- **Build and browser:** G51–G54.
- **Discoverability and help:** G56, G58.

**P2: valuable polish (22).**
- **Scheduled (Small):** G62 remote reply line, G63 "Answered in", G64 approvals record, G65 unattended-form note, G66 Everything tiering and blurbs, G67 settings row names, G68 destructive actions, G69 Change key, G70 card permissions, G71 computer-control wording, G72 LM Studio presets, G73 "Download a model for me".
- **Larger, later:** G60 Microsoft 365, G61 Remote Jarvis node, G74 Tools-page jargon, G75 chat-control words, G76 vocabulary canon, G77 consequence labels, G78 pack on/off, G79 mission cost note, G80 calm primitives as default, G81 read-folders setting.

**P3: future ecosystem (5).**
- G82 one catalog file.
- G83 curated/community directory feed.
- G84 a callable REST hookup tool.
- G85 vetted desktop-control pack.
- G86 bundled starter local model. This is the owner's call: it is the only way to make "useful work in five minutes" true for someone with no key and no CLI.

## 9. Implementation effort and dependencies (every P0/P1)

**There is no Large item among P0/P1.** Every one is a wording, wiring or small additive-field change over existing data.

| Gap | Title | P | Effort | Depends on | Wave · lane |
|---|---|---|---|---|---|
| G01 | Schedules record real outcome | P0 | Small | — | 1 · A |
| G02 | Status/home flag failed schedules | P0 | Small | G01 | 1 · A |
| G13 | Emergency stop really stops | P0 | Medium | — | 1 · A |
| G03 | Bell rule for unattended work | P0 | Medium | G01, G05 (shape) | 1 · B |
| G04 | Bell remembers after reopen | P0 | Medium | G03 | 1 · B |
| G18 | Session receipt data | P1 | Medium | — | 1 · B |
| G05 | Chat waiting for OK announced | P0 | Medium | — | 1 · C |
| G06 | Honesty note fixed | P0 | Small | — | 1 · C |
| G10 | Honest demo reply | P0 | Small | — | 1 · C |
| G14 | No "you run locally" for cloud | P0 | Small | — | 1 · C |
| G07 | Work line shows failures | P0 | Small | — | 1 · D |
| G08 | Green only when it works | P0 | Small | — | 1 · D |
| G09 | Mission headline partial | P0 | Small | — | 1 · D |
| G11 | Folder Open works | P0 | Small | — | 1 · D |
| G12 | Pack secrets masked/vaulted | P0 | Medium | — | 1 · E |
| G15 | Connect in chat works & continues | P0 | Medium | — | 1 · E |
| G16 | Stopped ≠ failed | P1 | Small | — | 2 · A |
| G19 | Helper card/row receipt words | P1 | Medium | G18, G16, G11 | 2 · A |
| G21 | Replies say where they ran | P1 | Small | G14 | 2 · A |
| G23 | Mission uses chat's words | P1 | Small | G09 | 2 · B |
| G24 | One set of approval words | P1 | Medium | G23 | 2 · B |
| G25 | Undo line on approvals | P1 | Small | G24 | 2 · B |
| G17 | One status word table | P1 | Medium | G01, G09 | 2 · C |
| G22 | App/browser step words | P1 | Small | G07 | 2 · C |
| G26 | Running-now list | P1 | Small | — | 2 · D |
| G27 | What Jarvis can reach | P1 | Small | — | 2 · D |
| G33 | Sign in/install doors on Accounts | P1 | Small | — | 3 · A |
| G34 | Ollama one truth, one press | P1 | Medium | — | 3 · A |
| G35 | Setup confirmation persists | P1 | Small | — | 3 · A |
| G36 | Home machine-check & connect-apps lines | P1 | Small | — | 3 · A |
| G57 | Model menu & settings in names | P1 | Small | — | 3 · A |
| G38 | Guide answers "what can you do" | P1 | Small | — | 3 · B |
| G40 | Media asks for key up front | P1 | Small | — | 3 · B |
| G41 | Refusals leave a fix button | P1 | Small | — | 3 · B |
| G42 | One Connections home | P1 | Medium | G08 | 3 · C |
| G43 | Catalog entries that install | P1 | Small | — | 3 · C |
| G45 | Setup needs before Connect | P1 | Small | — | 3 · C |
| G51 | Build pane chat retry/receipt | P1 | Small | — | 3 · D |
| G52 | Panes: neutral finished, needs-you alerts | P1 | Small | — | 3 · D |
| G53 | Sidebar summary failures/time | P1 | Small | — | 3 · D |
| G54 | Agent-browser approval copy | P1 | Small | — | 3 · D |
| G37 | Release page explains download | P1 | Small | — | 3 · E |
| G56 | Phone chat findable, one screen | P1 | Small | — | 3 · E |
| G58 | Help phone recipe honest | P1 | Small | — | 3 · E |
| G28 | Daemon failures in plain words | P1 | Medium | — | 4 · A |
| G39 | Chat knows apps by name | P1 | Medium | G15, G38, G45 | 4 · A |
| G20 | Run page as receipt + interrupted Continue | P1 | Medium | G18, G24 | 4 · B |
| G30 | "Iron Jarvis", not "daemon" (shell & chat) | P1 | Medium | — | 4 · B |
| G31 | Recovery visible | P1 | Small | G30 | 4 · B |
| G49 | Import explains itself | P1 | Medium | — | 4 · C |
| G50 | Import reads current files, respects off | P1 | Small | G49 | 4 · C |
| G59 | Settings/panels without engine words | P1 | Small | — | 4 · D |
| G47 | Model cards match what answers | P1 | Small | — | 4 · E |
| G48 | Hide REST dead end; "Add a tool" | P1 | Small | — | 4 · E |
| G29 | Chat failure line: headline, details, fix | P1 | Small | G28, G33 | 5 · A |
| G55 | Context refit disclosed | P1 | Small | — | 5 · A |
| G44 | One add/remove door (+ GitHub maintained) | P1 | Medium | G43, G08 | 5 · B |
| G32 | Background problems named, linked | P1 | Small | — | 5 · C |
| G46 | Gemini API key | P1 | Small | — | 5 · D |

**Wave plan.** Five waves; lanes inside a wave own disjoint files.
- `app/chat/page.tsx` and the lock-step chat pair (`chat_turn.py` + `routes/chat.py`) each have one owner per wave.
- Waves 1–3 alone bring a new user to "install, connect, understand what happened". Waves 4–5 finish the words and the doors.
- Look and feel stays exactly as it is: calm chat, quiet rows, theme tone tokens, no new boxes. Every UI change in the gap list says so.

**How each wave is run:**
1. Doer/reviewer lanes, with the reviewer reading the real diff and mutation-checking the tests.
2. One integrator updates `docs/HANDBOOK.md` (so the Guide stays truthful) and bumps the version.
3. The full suite runs once before the push.

**Two known traps:**
- G37 touches a workflow file, so its tag is pushed by hand.
- G30 and G08 change pinned strings, so they move their tests in the same change.

## 10. Do not build

- **No new notification service or "while you were away" page.** The bell, EventRecord and GET /audit already exist; seed the bell (G04).
- **No receipt database or receipt service.** The Session row, `session_result`, the chat route object and the tool ledger already hold it; add fields (G18).
- **No new universal reply component replacing RunResultCard, WorkLine and TurnReceipt.** Share the words (stepWords, statusWords, secondsText, providerDisplay) instead.
- **No marketplace backend, ratings, accounts or store page.** `list_connectors` + `add_mcp_pack` + the import scan + `app_connect` are the directory base (G83 later is just more catalog rows).
- **No second app catalog.** Extend `connectors/catalog.py` and later derive `/mcp/catalog` from it (G82).
- **No native Gmail, GitHub or Slack integrations.** Use maintained servers through the existing pack door (G43, G44).
- **No per-surface approval components.** Reuse ApprovalCard and its labels.
- **No new onboarding wizard.** FirstRunWizard was deliberately unmounted; use the empty-chat doors, checklist and OnboardingWelcome patterns.
- **No model-written "explain this error" call.** Typed fields (kind, status, failure_reason) give plain words deterministically (G28).
- **No "running now" backend route.** GET /sessions + /chat/threads running flags suffice (G26).
- **No new health checker.** Pack load records, /health and the selfcare readers already exist (G47 reads the router's answer).
- **No automatic switch to a detected Ollama, and no failover from a local model to the cloud to make first run "just work".** Explicit press only, per the never-auto-switch rule.
- **No Jarvis-specific remote protocol.** Reuse the http-task body, the remote-agent registry and the inbound route (G61).
- **No separate MCP permissions model.** Derive from `write_like`, quarantine and `auto_approve` (G70).
- **No glossary/tooltip system across the app.** Fix the words at the source (G74–G77).

## 11. Final verdict

**How close is Iron Jarvis today to the intended product?**
About halfway to two-thirds of the way. The engine is ready for real people: it recovers, survives restarts, refuses honestly and keeps a complete ledger. The chat page is close to the target experience.

A new user still hits developer-grade edges as soon as they leave the chat or step away:
- background results that say "ok" when they are not, or say nothing;
- an app they connected that their chat cannot use;
- a demo reply that contradicts itself;
- "Daemon offline";
- raw provider and tool ids;
- import and connections spread over several pages with protocol words on them;
- an Emergency stop that does not stop.

None of that is missing capability. It is the last mile of presentation and wiring.

**What is the smallest amount of work to cross from powerful developer system to polished general-user product?**
Waves 1 to 3 of this plan:
- 15 P0 fixes, almost all Small and none Large;
- then about 30 P1 changes that reuse what exists: one status word table, the session receipt fields chat already shows for its own turns, one approval vocabulary, honest connect-and-continue, and one Connections home with import surfaced.

That needs no new service, store or architecture. It means making every surface read the outcome, words and doors the daemon already has, so Schedules, missions, the bell, the Directory and the error lines say what chat already says. Waves 4 and 5 finish the plain-words sweep and the remaining doors. The P2/P3 items, Microsoft 365, a second Jarvis and a bundled local model, are real additions that can follow once the existing power is understandable.