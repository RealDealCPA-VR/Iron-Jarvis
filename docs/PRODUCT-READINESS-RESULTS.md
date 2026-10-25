# Iron Jarvis Product-Readiness Audit: Final Report (v1.350.2, HEAD 94043cfb) and Re-audits (v1.357.0, v1.359.0, latest v1.388.0)

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

## 0g. Re-audit after the closing wave (v1.388.0, 2026-10-25)

**What was re-checked, and how.** Since the last full re-audit (0d, v1.359.0), waves 10 and 11 (0e, 0f) closed its regressions, the IJ harness work (v1.363.0 to v1.387.2) added Build's coding sessions, and the closing wave (v1.388.0, lanes 12A and 12B) worked through the open gaps from 0d.4: plain failure words at every door, one Sign in press, every built-in tool in words, a waiting chat seen where you are, Continue after connecting elsewhere, the phone honest about apps, and one phone message for a waiting workflow.

Seven re-audit lanes re-marked all 213 checklist items against the release commit `4c88c2cd`, in a read-only copy of the tree. They used the same groups as before: A-B, C-D-O, E-J, F-K, G-N, H-I-M and L. Each lane:
- re-read the code behind every item in its sections and checked each open gap against its acceptance;
- ran the pinned tests for its areas a few files at a time (about 4,500 pytest and 3,500 vitest test runs in all, every pinned test green; one lane ran the whole dashboard suite by mistake, and its 8 failures were the unbuilt add-on bundle and load flakes that pass alone);
- ran read-only probes where reading was not enough: the app matchers and the tool selector on 54 everyday phrasings (byte-identical output at v1.361.0 and v1.388.0), the Connect door on 55 sentences, the failure-words classifier on 32 real error shapes, a fresh scratch home through the real app factory, and a jsdom copy of Build's real page;
- sent read-only GET requests to the running app, which reports v1.387.0, so the closing wave is not on the user's install yet.

Old per-item marks were not kept, so each lane marked from scratch and explained every move against 0d.3. Where two lanes scored one category, I used the median, rounded down, as before.

### 0g.1 Overall score: **77 / 100** (55 at the first audit, 70 at v1.357.0, 74 at v1.359.0)

The twelve categories average 77.9. I score 77, one point lower, for the same two reasons as in 0d.1:
- **Several of the larger pieces have never run against the real thing:** Microsoft 365, GitHub's remote server, Gmail and Drive, a Gemini key, Ollama and LM Studio, the task door between two PCs, the tray notices and the Sign in press in the packaged app.
- **The closing wave is not on the user's install yet.** It runs v1.387.0; v1.388.0 is published.

155 of 213 items pass, up from 152. None fail. Items moved both ways (0g.3 names them). The ones that moved away from PASS are not fixes that were undone (0g.5): they are new surfaces the checklist now reaches, mostly Build's IJ harness, and two older gaps the 0d lanes understated.

What the closing wave fixed, confirmed in the code by the lanes:
- A failed task reads in one plain sentence on the run page, Schedules, the bell, the phone, the desktop notice, workflow steps and goals, with the technical text folded under Details.
- One Sign in press for Claude Code and Codex in Connections, after Rescan, above the chat box and on the Agents page; no terminal recipe remains for either.
- Every registered built-in tool reads in words on approval cards, the work line, the folded count and the desktop notice, generated from the registry and drift-checked in CI.
- A chat waiting for an OK is listed in the bell from another page of the same window; tray notices for failed unattended runs (wave 10) hold.
- After connecting an app in the Directory, by signing in, with a key or by turning an extension on, the chat that asked shows Continue.
- The phone says plainly how a connected app can be used.
- Replies name the user's own machine ("answered by GLM 4.6 on DGX Spark"), and the access summary follows the readable-folders setting.

What holds the score at 77:
- **Build's IJ harness is behind the rest of the app.** It is invisible outside Build (Running now, the status orb and the home line say nothing is running while a reply runs for an hour), its receipt leaves out the approvals given and the refit, its failure line says "Press Retry" with no Retry and keeps no technical detail, it cannot use connected apps, and its name ("IJ harness") is engine-room on a primary page.
- **Common phrasings still miss.** "summarize my latest emails" goes to web search; "what apps can you work with?", "what are missions" and "what is build for" arm nothing; chat never suggests a mission; about 1 in 4 of a deliberately tricky set of sentences offers a wrong Connect door.
- **Older dead ends now named.** A risky website step from a chat or agent run is refused with "approve it, then try again" while nothing is waiting to approve; a remote agent's task has no end state, a 202 reads "done", and the mission card shows the untrusted-content header instead of its answer; a failed @-agent says only "couldn't answer".
- **Setup still needed.** Gmail, Google Drive, OneDrive and Dropbox need a one-time Google/Microsoft developer setup; Slack needs a Slack-app token; a user with no key and no CLI still needs Ollama and a 2 GB download (G86, the owner's call).
- **One regression** (0g.5), fixed in v1.388.1.

### 0g.2 Category scores

| Category | First audit | v1.357.0 | v1.359.0 | Now | Why |
|---|---|---|---|---|---|
| Action clarity | 60 | 77 | 83 | **85** | Every built-in is worded, failures are plain at every door, replies name the machine, one Sign in press, and the harness's finish line says whether the code worked. Held back by: the harness is not named on the chat line, four surfaces state the same run's model and place differently, Build replies hide approvals and refit, @-agent failures give no reason, remote tasks have no end state, and the website risk door is a dead end. (A-B 85, L 86) |
| Execution visibility | 62 | 76 | 78 | **82** | B passes in full. Failed unattended runs and long Build turns reach the tray; a waiting chat on another page is in the bell. An hours-long IJ harness reply leaves Running now, the orb and the home line saying nothing runs; chat turns, chat-started helper runs, missions, queued agent tasks and media end silently in the tray. (A-B 81, L 84) |
| Connections | 42 | 58 | 66 | **72** | Continue after connecting elsewhere, the phone's honest words, one Sign in press, endpoint keys used on every background read, fewer false doors. Still: Gmail and Drive need a developer setup, Slack an app token with no warning, three "connected" counts and no single view, two model-server forms (Fleet's has no key field), a saved endpoint's key cannot be changed, the remote node unproven, web services Bearer-only, the harness cannot use connected apps. |
| Import/discovery | 45 | 74 | 76 | **80** | Every found server now gets a line saying what it is (G49). Still: nothing is offered at first run, and some lines read "(index)" or "(cmd)". |
| First-run experience | 52 | 70 | 74 | **76** | The terminal sign-in recipe is gone from every first-run surface and failures are plain. Still: no five-minute path with no key and no CLI (G86) and the free Gemini key is not offered as free, subscribers without the CLI install it from docs, sign-in and the model download happen in a terminal, four big apps need a developer setup, the key door prints raw Test text, and the terminal-link regression (fixed in v1.388.1). |
| Error/recovery UX | 60 | 70 | 75 | **80** | Plain run words with Details, one Sign in press, a way out of a safety refusal, recovery that says so, waiting chats found. Still: the IJ harness lacks Retry, Sign in and Details while saying "Press Retry"; chat hides Iron-Proxy's exact account sentence behind "Press Retry"; a helper's failure reads raw on the mission log and Build helper rows; rare phone crash lines and @-agent failures. |
| Consistency | 40 | 60 | 68 | **70** | One model name, one generated tool-words table, one failure rule, one Sign in press, one notice per outcome. Still: the Session board's red "Failed" lane for stopped runs, 73 boxed error notes, the mission screen's own ask card, Slack and email in two places, the phone's "FAILED". New with the harness: different failure UI, "Compact" where chat says "Summarize", "session" with two meanings. |
| Discoverability | 50 | 63 | 65 | **66** | Continue after connecting, the phone's words, fewer false doors. Still: "summarize my latest emails" goes to web search, feature questions arm nothing, chat never suggests a mission or Build, coding sessions sit behind a tab named "IJ harness", and the Guide answers mission questions from the developer manual. (E-J 65, F-K 68) |
| Beginner friendliness | 45 | 63 | 69 | **71** | The claude-cli preflight, "tool packs", "panes", "the pack", raw tool ids and Help's stale catch-all are gone. New: "IJ harness", "worktree", "Compact", Iron-Proxy in the harness accounts. Unchanged: "Local fleet", "Rescan local CLIs", the OAuth client id, Channels / SMTP / IMAP, and the nav's Sentinels, Reflexes and Artifacts. |
| Trust | 55 | 70 | 76 | **79** | Reading follows the setting, the machine is named, the run page says local or cloud, and the harness added strong safety mechanics (once-only safety cards, the git guard, command-file asks, worktree branches, all-or-nothing Rewind, the account pin, per-model proxy egress). Still: "Don't ask" does not say it also sends with connected apps, the mission screen never says local or cloud, Local only says "this PC" while LAN hosts pass, an @-agent round has no Stop, and harness replies are invisible to Running now. |
| Stability | 85 | 92 | 93 | **94** | All 14 items pass, including the real-daemon update rehearsal with the new `failure_detail` column. Small: an @-agent round has no Stop; kept `&` jobs survive a crash by design. |
| Marketplace/capability readiness | 58 | 76 | 78 | **80** | One catalog and one door; needs, permissions, on/off, repair and sign-in carried as data; the false-door regression fixed. Still: models, channels, agents, web services and fleet nodes keep their own row shapes; key verdicts reset on restart; intent matching is by name only and stops at the phone and Build. |

### 0g.3 PASS / PARTIAL / FAIL (first audit → v1.357.0 → v1.359.0 → now)

| Section | Items | PASS | PARTIAL | FAIL |
|---|---|---|---|---|
| A. Actions self-evident | 33 | 3 → 13 → 24 → **22** | 30 → 20 → 9 → **11** | 0 |
| B. Execution receipt | 24 | 10 → 22 → 23 → **24** | 14 → 2 → 1 → **0** | 0 |
| C. Connections | 21 | 2 → 10 → 12 → **13** | 17 → 11 → 9 → **8** | 2 → 0 → 0 → 0 |
| D. Import | 16 | 1 → 13 → 14 → **15** | 15 → 3 → 2 → **1** | 0 |
| E. First run | 11 | 1 → 5 → 6 → **6** | 10 → 6 → 5 → **5** | 0 |
| F. Natural-language discovery | 6 | 0 → 0 → 1 → **3** | 6 → 6 → 5 → **3** | 0 |
| G. Error & recovery | 10 | 4 → 7 → 8 → **7** | 6 → 3 → 2 → **3** | 0 |
| H. Consistency | 15 | 2 → 9 → 9 → **9** | 13 → 6 → 6 → **6** | 0 |
| I. Engine room | 14 | 2 → 10 → 12 → **11** | 11 → 4 → 2 → **3** | 1 → 0 → 0 → 0 |
| J. Discoverability | 5 | 0 → 2 → 2 → **2** | 5 → 3 → 3 → **3** | 0 |
| K. Trust | 11 | 1 → 5 → 8 → **8** | 10 → 6 → 3 → **3** | 0 |
| L. "Did it work?" | 14 | 2 → 9 → 8 → **11** | 12 → 5 → 6 → **3** | 0 |
| M. "What does this mean?" | 8 | 0 → 3 → 3 → **2** | 8 → 5 → 5 → **6** | 0 |
| N. Stability | 14 | 11 → 14 → 14 → **14** | 3 → 0 → 0 → **0** | 0 |
| O. Marketplace readiness | 11 | 2 → 8 → 8 → **8** | 9 → 3 → 3 → **3** | 0 |
| **Total** | **213** | **41 → 130 → 152 → 155** | **169 → 83 → 61 → 58** | **3 → 0 → 0 → 0** |

The moves. The v1.359.0 lanes' per-item marks were not kept, so each move below is inferred from 0d's text and the lanes' own evidence:
- **To PASS:**
  - A "did it fail", "does it need me", "which machine", "which tools" and "scheduled work": tray notices and one notice per outcome (wave 10), the waiting chat (12B), the machine named (G21), every built-in worded (12A).
  - B "machine/node": `route_label` covers custom and Ollama endpoints, and the run page says local or cloud.
  - C, most likely Claude Code or Codex: the one Sign in press (12A).
  - D "explains what was found": every found server gets a line (G49).
  - F two items, "available" or "configured", and "resume after connecting": the phone's honest words, the sign-in state rule and Continue after connecting elsewhere (12B).
  - K "what access" and "which machine": reading follows the setting; G21.
  - L "tool call", "scheduled/background" and "approval flow": 12A words, tray notices, one phone line.
- **Away from PASS:**
  - A "is it working", "what is it doing" and "background work": one cause. Build's IJ harness replies are invisible to Running now, the orb and the home line.
  - A "Build": the harness receipt never shows approvals or refit, although the reply line saves both.
  - A "browser" and "approvals": the website risk door's dead end from chat and agent runs. It was already true at v1.359.0 and understated then.
  - A "why did it fail" and "failures": a failed @-agent gives no reason. Also older.
  - G "technical detail available": the harness keeps none, and chat's is a hover only.
  - I "harness": "IJ harness" labels one of Build's two views and appears in the bell.
  - K "what is it doing": the harness, as above. K "what will need approval": a stricter reading of unchanged words ("Don't ask" runs connected-app actions without asking and does not say so).
  - M "ambiguous labels": "IJ harness", "Compact", and "session" with two meanings.

### 0g.4 Gaps from 0d.4 and their remainders

| Gap | Now | What remains |
|---|---|---|
| G21 Replies say where they ran | **Met** | The helper row names no machine for a named model on the user's server; the mission receipt never says local or cloud. |
| G28 Failures in plain words | **Met at every run door** | A helper's failure to its parent (mission log, Build helper rows), chat's Iron-Proxy account refusals, phone crash paths, the Channels row, and @-agent failures. |
| G33 Sign in from Accounts | **Met** for Claude Code and Codex | Grok has no Sign in press. |
| G03 / G05 Notices and waiting chats | **Met** | Missions, queued agent tasks, media, chat turns and chat-started helper runs are not toasted from the tray. |
| G15 Continue after connecting | **Met** | Newest reply only; a connected Drive still needs a memory base. |
| G22 / G24 Tool and approval words | **Met** | Custom tools by id (by design); "channel" and "computer control" in generated words; `config_undo`'s card. |
| G39 Apps by name | **Partly met, by decision** | The phone is honest and offers an agent hand-off; arming is by name, in the chat stream lane only, never in Build. |
| G41 Connect-door misfires | **Partly met** | The 0f.3 trade-offs still offer a door, plus "Should I move my team from Slack to Teams?" and a Google Maps presentation. |
| G49 Import explains itself | **Met** | Polish: "(index)", "(cmd)". |
| G61 Remote Jarvis node | **Partly met** (unchanged) | Never run on two PCs; no Windows Firewall help. |
| G76 One vocabulary | **Partly met** | The canon was not extended for the harness ("IJ harness", "Build session", "session" twice); "channel" is retired in the canon but not on the scanners' list (about 15 lines survive); the nav renames are the owner's call. |
| G81 Readable folders | **Met** | — |
| G54 Website approvals resume | **Still open** | Approving does not resume; from chat and agent runs nothing is even queued. |
| G62 Remote end state | **Partly met** | See 0g.1. |

### 0g.5 Regressions found

1. **A link to a terminal opened the IJ harness instead** (since v1.385.0). Build keeps the view a window last showed in module state, and the `?focus=` branch returned without switching to Terminals. Once the harness had been shown in a window, every in-app terminal link landed on it and hid the pane: "Open the Build terminal" after Sign in or the starter model, Microsoft 365's sign-in, the bell's "a terminal needs you", Creative's "Open in Build" and the Iron-Proxy sign-in. The pinned test missed it because the test setup resets that cache. Found by lane E-J with a copy of the pinned test; **fixed in v1.388.1** (a terminal link starts on Terminals from the first frame), pinned with the same-window case and a render count, mutation-checked.

No fix from v1.359.0 to v1.388.0 was found undone. The lanes' other findings are gaps on surfaces added since (the IJ harness) or older gaps now named; 0g.3 lists them.

### 0g.6 Engine-room words still reaching users

On primary paths:
- "IJ harness" (Build's view switch, its sidebar label, the bell's "A change to a harness file is suggested", "relaunch the harness" in terminal refusals).
- "worktree" in the harness's branch panel and sidebar; "Compact" in the harness where chat says "Summarize".
- Raw failure text: a helper's "crashed: ProviderError: codex-cli: …" on the mission log and Build helper rows; "Pending approval id=appr_…" from the website risk door; phone crash lines with class names.
- The first-run key door and Connections print the key Test text raw ("could not reach anthropic: ConnectError: [Errno 11001]…", "openai probe returned HTTP 500: {json}", "served through the logged-in claude CLI").
- Connections: "Subscription & local providers", "Rescan local CLIs", "Sessions use this provider by default", an always-shown "Server type: OpenAI-/Anthropic-compatible", and `<provider>_oauth_client_id` on the Gmail and Drive paths; Grok's "run `grok login`".
- Channels: "Open Channels", "the Channels page", SMTP and IMAP; "notification channel" in generated tool words.
- The nav's Sentinels, Reflexes, Artifacts and "Local fleet"; "Your model fleet" on a first-run door; the Fleet page's "Add a node" form.
- The status orb's "Thinking — a session is in flight"; "Attaching to sessions…"; "this action has no safe inverse".
- A chat panel's raw "done" label over a remote's failed reply; the mission card's untrusted-content header in place of a remote's answer.

Advanced or rare (unchanged): "HTTP server", "http-task (task API)", "Config (JSON)", "An MCP memory server", "ollama pull llama3.2", the extension fix's PATH / npx advice, "dashboard did not start".

### 0g.7 Not built, by the owner's decision

Unchanged: G83, G85 (control of other desktop apps) and G86 (a bundled model for a user with no key and no CLI). The nav renames (Sentinels → Watchers, Reflexes → Rules, Artifacts → What agents made) are proposals; the canon's rename procedure supports them once rows are added, and "Rules" may collide with Build's folder rules.

### 0g.8 Never proven live

- None of the closing wave on the user's install (it runs v1.387.0) or in the packaged app: the Sign in press on a really signed-out CLI, the waiting-chat bell, Continue with real OAuth, the phone's notes on the real phone, the tray notices as real Windows toasts.
- Microsoft 365 sign-in; GitHub's remote server; Gmail, Drive, OneDrive and Dropbox OAuth; Slack, Notion and Stripe with real accounts; a Gemini key.
- Ollama, LM Studio and llama.cpp detection and the one press (none installed on this PC).
- The task door and the LAN setting on two physical PCs, including the Windows Firewall prompt; a real remote answering 202.
- The website approval queue on a real site; the git guard against real GitHub pushes; Iron-Proxy rotation with two signed-in accounts.
- A clean-profile packaged first run, and the five-minute timing with a real new user.

### 0g.9 Verdict

Iron Jarvis is at 77. The closing wave did what it set out to do: the failure words, the Sign in press, the tool words, the waiting chat and Continue all hold in the code, and nothing that worked was undone apart from one link regression, fixed in v1.388.1. The score is held by three things: Build's IJ harness, which was built after the last audit and has not yet been brought up to the rest of the app's honesty and visibility rules; the common phrasings chat still misreads ("summarize my latest emails", "what can you work with"); and setup the owner has chosen not to remove or that needs the user's accounts to prove. The fastest points left are in the harness (visible in Running now, Retry and Details on failure, approvals and refit on its receipt, an everyday name) and in the matcher (mail and feature questions).

---

## 0f. After wave 11 (v1.361.0)

**What was done.** A final regression hunt over v1.359.0..v1.360.1 confirmed 15 things that worked at v1.359.0 and broke in wave 10, and rejected 3 more. Wave 11 (lanes 11A, 11B and 11C, then the integrator) closed all 15. Each entry below was re-checked against the tree after the integrate changes in two ways:
- **Pins.** The lanes' and the integrator's pins: the real app factory, both real chat lanes, the real dashboard components, and the real `desktop/main.js` / `preload.js` functions under node.
- **Probes.** The hunters' own probes, re-run against the current tree (0f.2).

Nothing here has run in the packaged app yet. Section 0d's scores and counts are left as they were. This section does not re-score.

### 0f.1 The regressions (the final hunt, v1.360.1)

| # | Regression | Now | Pinned by |
|---|---|---|---|
| 1 | A pop-out window's bell went silent. With the main window in the tray, most notices raised no desktop toast. | **Fixed.** ONE window speaks: the main window while it is open, else the first pop-out still open (`main.js announcePopoutSpeaker` → `preload popout.speaks()` → `lib/desktopShell.quietWindow`). The tray watchers count a speaking pop-out as an open window (`speakingWindowOpen`). (integrate) The "This PC" bridge follows the same rule. A clean schedule's "done" now reaches a toast from the speaking pop-out too. A quiet window still records each line, so nothing repeats when it becomes the speaker. | `dashboard/__tests__/no-notice-lost-v13610.test.tsx` (1), `wave11-seams-v13610.test.tsx` (a) (b), `tests/test_no_notice_lost_v13610.py` |
| 2 | A scheduled workflow whose `on_failure: skip` step failed told the phone only "done". A halted one lost the failing step's reason. | **Fixed.** The Schedules row reads Part failed. Every destination gets one line naming the step and its own words, e.g. "…ran, but its step “Fetch” could not finish: The inbox could not be reached." A halted run's FAILED line names the step that stopped it. This PC stays quiet only when every named step has its own run that the bell rings. A tool step keeps This PC's line. A clean run, even after a retried step, keeps "done". | `tests/test_no_notice_lost_v13610.py`, `tests/test_integrate_wave11_v13610.py` (a) + fixture `wave11-seams-v13610.json`, `wave11-seams-v13610.test.tsx` (a) |
| 3 | A failed workflow was not pinged, badged or toasted when an earlier attempt of one of its steps had been pinged. | **Fixed.** `workflow.completed` carries `failed_session`: the run of the step that ended it, or "" when that step has no run. Only that step's notice stands in for the workflow's (bell `standsInFor`, tray `endedBy`). An event an older build wrote keeps the v1.360.0 rule. | `no-notice-lost-v13610.test.tsx` (3), `tests/test_no_notice_lost_v13610.py` (the tray plan under node) |
| 4 | With a window open, an ask's only desktop notice was a bare count. | **Fixed.** When exactly one ask is new, the "N pending" ping names it in the bell row's words: the workflow and its question; the run, its action and its task; or the chat's tool. Several at once keep the counts. | `no-notice-lost-v13610.test.tsx` (4) |
| 5 | The Connect door was gone for everyday own-data requests ("on <app>", "<app>:", a chat verb). | **Fixed.** Covers place words with a first-person or question signal, chat verbs, "use <app> to", and labels. Research, topic and explain sentences are still read first. | `tests/test_matcher_tables_v13610.py` (one table, both directions), `tests/test_integrate_wave11_v13610.py` (b) (every row through both chat lanes) |
| 6 | "my <app> <thing>" lost the door when the thing was outside a closed noun list. | **Fixed.** Covers owned nouns after a possessive, and any non-modifier noun after an acting verb plus "my <app>". | same |
| 7 | The second app in "X and Y" lost its door. | **Fixed.** One door names both ("Connect Google Drive and Dropbox in Settings"). "compare Slack and Teams" offers none. | same |
| 8 | Asking to use Pixio lost "Connect Pixio in Settings". | **Fixed.** An API-key service used as the instrument counts. "is pixio better than midjourney?" offers nothing. | same |
| 9 | The Guide was not armed for Handbook how-tos outside its word lists, or when "Jarvis" was in an earlier sentence. | **Fixed.** All 15 of the hunt's sentences are armed. "how do I make a cake" and "how do I fix my bike chain" are not. | same |
| 10 | Lower-case "my calendar … in outlook" lost the Microsoft 365 door, and the model was told never to guess. | **Fixed.** (integrate) "In my outlook, the market will recover" names no app. A sentence that opens with "in/from my/our outlook" is the idiom for "in my view". (re-review) That holds with or without a comma: "in my outlook the market will recover" and "From my outlook as an investor this is risky" had kept a new false door that neither v1.359.0 nor v1.360.1 gave. The app's own things after the word ("In my outlook inbox, …", "from my outlook account, …") and a place in the middle of a sentence keep the door. | same + `test_the_view_idiom_is_not_the_app_on_either_lane` |
| 11 | The approval chip's menu was not placed again when its rows grew after it opened. | **Fixed.** It is measured again whenever the reading sentence changes; focus is still given once. | `labels-say-it-once-v13610.test.tsx` (1) |
| 12 | The substitute receipt said the machine twice. | **Fixed.** "answered by Spark B (you asked for Claude Code) · local". | `labels-say-it-once-v13610.test.tsx` (2), `wave11-seams-v13610.test.tsx` (c) |
| 13 | OpenCode on "<server>/fleet" lost its name on the run page and under "Ran on". | **Fixed.** The run page reads "OpenCode · Your model server" (the model list's words); "Ran on" reads "your model server on OpenCode". | `labels-say-it-once-v13610.test.tsx` (3), `wave11-seams-v13610.test.tsx` (c) |
| 14 | An Ollama "fleet" pick was named after a detected model ("llama3"). | **Fixed.** Every surface says "your model server" or the machine's own name. (integrate, lane 11C's note) "Ran on" no longer places any Ollama run on a detected model's row: "qwen3 on llama3" had read so since v1.359.0. | `labels-say-it-once-v13610.test.tsx` (4), `wave11-seams-v13610.test.tsx` (c) |
| 15 | The run page said "· cloud" for a run the router refused before sending anything. | **Fixed.** The daemon's receipt gives `on_this_network: null` unless `agents/outcome.reached_a_model` (a step, tokens, a tool call, or a dispatch event). The run page reads `runPageWords.runReachedAModel`. | `tests/test_run_where_reached_v13610.py`, `run-page-reached-v13610.test.tsx` |

### 0f.2 The hunt's own probes, re-run against the tree

- **Notices** (`hunt/notices/vt/notices.probe.test.tsx`, given the payload the daemon now sends and a shell that names the speaker): the current tree matches v1.359.0.
  - The retried-then-failed workflow gets two pings and a badge of 1.
  - The pop-out in the tray pings the parked question and the failed mission.
  - The pending ping now also names the ask: “Nightly” asks “Send it?”.
- **Doors** (the skeptic's 31 own-data sentences): every one gets its door, except "Slack announced a new pricing plan today". That was a false door at v1.359.0, kept door-free on purpose. Over the hunters' wider lists (251 sentences), the doors not given back are deliberate verdicts:
  - Bare "my calendar" with no calendar app connected (v1.360.0: the words do not say which app).
  - "what's new in slack / on github" stays research, as v1.360.0 pinned "what's new in Notion…".
  - "how much revenue did stripe show last month?": an app as the subject of a question is also how news is asked.
- **Guide** (the skeptic's 17 sentences): all 15 how-tos are armed; the cake and bike controls are not. The hunters' wider lists hold 51 bare how-tos with no Jarvis signal ("how do I set a reminder", "how do I set it up"). These stay unarmed, as v1.360.0 decided (G38: the Guide is for questions about Iron Jarvis).
- **Receipt and names** (`hunt/mr`): every substitute and failover line names the machine once. The only differences from v1.359.0 are v1.360.0's intended alias naming, plus one: with a detected llama3 row, "Ran on" for an `ollama/llama3` run read "llama3" and now reads "llama3 on Ollama", like every other catalog shape.
- **Refused run** (`hunt/sk/probe_cloud_refused.py`): `on_this_network` is now `None`, and the run has no step, token or tool call.

### 0f.3 What this wave leaves

- **Untested on the real app.** The pop-out speaker (IPC from main.js to preload) and the tray watchers are proven through their real functions under node, never in the packaged Electron app.
- **Edges of the speaker switch.**
  - A pop-out that loads while the main window is already in the tray pings the whole pending count once, as the main window does on every reopen.
  - Each window keeps its own events socket, so a switch relies on the quiet window having recorded the same events.
  - A pop-out stuck on a failed page load still counts as an open window, so the tray watcher stays quiet for it.
- **Two lines, on purpose.** When a skipped agent step and a skipped tool step are both named, This PC speaks as well as the bell. That is two notices naming different steps, chosen over losing the tool step's failure.
- **Cancelled scheduled workflow.** One cancelled mid-run still reads "ok — workflow ran" (from before this wave).
- **Matcher trade-offs** (lane 11B's review; each a quiet, wrong "Connect X in Settings"):
  - News asked with "on <app>" ("what's the latest on Slack?").
  - Research in label form ("Notion: what is it?").
  - "I need a break from slack".
  - Coordination spillover ("I love notion and my gmail is full").
  - Statements such as "we built our startup on stripe", "I love the new dark mode on slack" or (re-review) "my team moved from slack to teams last year": `_SWITCH_RX` vetoes "switching / migrating / moving away or off", not a plain "moved from".
  - A capitalised "In my Outlook, …" still names the app.
  - "Jarvis, how do I change a tire?" arms the Guide (the name in the same sentence, accepted since v1.360.0).
- **Run page and receipt can differ.** For a cloud call that went out and then failed before any step, token or tool call, the run page says nothing while the receipt says cloud.
- **Raw id as a group name.** A model list names a fleet node's group by its raw id ("fleet-spark · Spark B"). It did at v1.359.0 too (the hunt's names probe).

---

## 0e. After wave 10 (v1.360.0)

**What was done.** Wave 10 (lanes 10A, 10B and 10C, then the integrator) worked through the final re-audit's 8 regressions (section 0d.5) and its 7 open gaps (0d.4 and 0d.7). Every entry below was re-checked against the tree after the integrate commit. The checks used the real app factory, the real chat lanes, the real dashboard components and the real `desktop/main.js` functions under node. None of it has run in the packaged app yet: the user's install is still v1.354.0. Section 0d's scores and counts are left as they were. This section does not re-score.

### 0e.1 The regressions (0d.5)

| # | Regression | Now | Pinned by |
|---|---|---|---|
| 1 | Two desktop notices for one failed or needs-you scheduled run while a window is open; two bell rows for one failed scheduled workflow (v1.351.0, G03) | **Fixed.** While a window is open, the bell's ping is the one desktop notice. `comm/notifier.bell_pings` is the bell's rule on the daemon side, and one fixture holds it equal to the dashboard's. "This PC" is quiet for those outcomes, for a workflow step, and (integrate) for an agent's ask and a parked workflow's question. A run a schedule started is announced by that schedule's own line on every destination. A failed workflow folds its step into one bell row. In the tray, main.js reads `GET /audit` and raises one OS notice per failed or unfinished run, in the bell's words. Its body falls back to the bell's own sentence. A goal schedule whose run ended needing the user reads "Needs you" on its row (integrate). | `tests/test_one_notice_v13600.py`, `tests/test_integrate_wave10_v13600.py`, `dashboard/__tests__/one-notice-v13600.test.tsx`, `wave10-seams-v13600.test.tsx` (the real daemon's events, audit and waiting runs fed through the real bell) |
| 2 | The Connect door read everyday and research sentences as the user's own data (G41) | **Fixed.** `connect_offers` now needs an own-data signal. "stripe the table rows", "explain how postgres indexes work", the sentry story and the LinkedIn post offer nothing. "list my github repos" and "check my stripe payments" still offer the door. | `tests/test_chat_matches_meant_v13600.py` (probe tables in both directions, 20+ each way, and the re-audit's four probes through both chat lanes), `tests/test_integrate_wave10_v13600.py` (b) |
| 3 | An Outlook user who says "check my inbox" was offered Gmail | **Fixed.** Mail and calendar words mean the connected app of that kind (`resolve_kinds` in `_gather_grounding`, both lanes). With Microsoft 365 connected, its tools are armed (stream lane) and no Gmail door appears. With no mail app, ONE door names both choices. "My calendar" with no calendar app offers nothing. The door opens where `/marketplace` (the Directory) lands. | `test_an_outlook_user_who_says_check_my_inbox_gets_microsoft_365` and `test_a_fresh_install_check_my_inbox_offers_both_mail_apps_once` (both lanes), `tests/test_integrate_wave10_v13600.py` (b) |
| 4 | "Reads files anywhere on this PC without asking" after reading was limited (G81) | **Fixed.** `readScope` / `readsLevelLine` (`lib/permissionLevels`) is the one decision. The chat's approval chip (on chat and in the Build terminal), the Access summary's Approval level row and (integrate) an agent's approval choices follow it. The chip reads `GET /settings/access` only on pointer, focus or open. | `dashboard/__tests__/reads-follow-setting-v13600.test.tsx`, `setup-reads-v13600.test.tsx` |
| 5 | A helper run's verdict lost its colour and could be cut off (v1.358.0) | **Fixed.** The verdict sits outside the truncating part of the settled row, in its tone token: "failed" and "could not finish" in danger; "needs you", "part failed" and "hit its step limit" in warn; "stopped" uncoloured. The full line is the row's title. | `one-notice-v13600.test.tsx` section (2) |
| 6 | The Guide rule armed the app-help lookups for how-to questions not about Iron Jarvis (G38) | **Fixed.** The lookups arm only for a question about Iron Jarvis: its name, a feature or page word in its own sense, "here", "this app". "how do I connect my printer to wifi", "how do I install node" and similar arm nothing. | `tests/test_chat_matches_meant_v13600.py` (the ABOUT_JARVIS and NOT_ABOUT_JARVIS tables plus the page-sense tables) |
| 7 | Sessions' armed "Clean up now?" was grey (G80) | **Fixed.** The armed press wears the danger cue, like "Clear finished" beside it. | `one-notice-v13600.test.tsx` section (3) |
| 8 | One model, two names: "Your model server" in the menu, "fleet" on the reply, the helper row and card, and the run page | **Fixed.** The reply's `route.label` names the user's machine on both lanes (`chat_turn.route_label`). Every list and card hands the `/models` catalog to the one rule (`modelRowName` / `answeredModelName` / `ranOnWords`), via lane 10B's patch, which the integrator applied. The run page's Model field follows it too (integrate: it read "DGX Spark · fleet"). Everything reads "DGX Spark", or "your model server" for a machine nobody named. | `dashboard/__tests__/model-server-name-everywhere-v13600.test.tsx` (with a scan for any call that leaves the catalog out), `wave10-seams-v13600.test.tsx` (c) (the real route and `/models` rows), `tests/test_integrate_wave10_v13600.py` (c) |

### 0e.2 The open gaps (0d.4, 0d.7)

| Gap | Now | Why / what remains | Pinned by |
|---|---|---|---|
| G21 Replies say where they ran | **Met (v1.360.1)** | A reply from the user's own model server says "on DGX Spark" / "answered by DGX Spark", and so do the run page, the helper card and row, and the menu (regression 8). v1.360.1 closed the last piece: a finished run's Model line on the run page ends "· local" or "· cloud" in the chat receipt's own words (`TurnReceipt.replyWhere`, reused through `runPageWords.runWhere`), decided by the run's own receipt `on_this_network` (the read the files panel already makes; no new request), else the `/models` row for its provider and model. It says nothing when unknown, for the demo model, while a run is still going, or for a run stopped before it reached a model (nothing was sent). Doer, independent reviewer (4 of its 11 mutations found pin gaps, closed) and a ship-step guard. | `test_both_lanes_name_the_machine_that_answered`, `wave10-seams-v13600.test.tsx` (c), `dashboard/__tests__/run-page-where-v13601.test.tsx` (13 cases) |
| G49 Import explains itself | **Met in code** | A found server outside the catalog says what it is, from what the import knows: its name in words, plus the program it runs on this PC or its address's host, for example "QuickBooks Desktop · runs a program on this PC (quickbooks-desktop-mcp)". Once it is added and started, its tool count and names replace that line. Nothing is invented: the catalog's description still wins. Not yet re-run against the six servers found on the user's PC. | `tests/test_found_tools_words_v13600.py`, `dashboard/__tests__/found-tools-say-what-v13600.test.tsx` |
| G61 Remote Jarvis node | **Unchanged: partly met (met in code, unproven)** | As in 0d.4: never run on two physical PCs, and a Windows Firewall block is neither detected nor explained. | (unchanged) |
| G76 One vocabulary | **Met** | The daemon scan reads every route module in full, plus every ANSWER position across the whole daemon: raised messages, detail / reason / fix / note / error / summary / title / message / why values, and sentences named like `*_MESSAGE`. All 60 lines lane 10C parked were reworded by the integrator, and `PENDING` is empty. "plug-in" is now in the retired list that all three scans read. The re-audit's examples now say "4 of 4 extensions running", "0 open terminals", "the extension's server couldn't be reached … Tools → Connected extensions", "A memory base served by an extension you added." and "extension tools". | `tests/test_vocabulary_daemon_answers_v13600.py`, `tests/test_vocabulary_beyond_dashboard_v13580.py`, `dashboard/__tests__/vocabulary.test.ts`, `tests/test_integrate_wave10_v13600.py` (d) |
| G83 Curated directory feed | **Not built** (owner's decision) | Unchanged. | — |
| G85 Desktop-control pack | **Not built** (owner's decision) | Unchanged. | — |
| G86 Bundled local model | **Not built** (owner's decision) | Unchanged. | — |

### 0e.3 Remainders from 0d.4 that this wave touched

- **G03 (the bell):** a failed unattended run now raises one OS notice while the app sits in the tray. The G05 half is unchanged: a chat waiting for an OK while the user is on another page of the same window is still listed only by the sidebar's dot.
- **G41 and G81:** fixed (regressions 2, 3 and 4 above).
- **Unchanged:** G15 (Continue follows only a token saved in the chat's own card), G22 / G24 (rarer built-ins' words), G28 (unrecognised failures outside chat), G33 (the Rescan note's terminal recipe) and G39 (apps by name on the chat page only).

### 0e.4 Known limits this wave leaves

- **Untested on the real app.** None of this has been seen in the packaged app or the Windows tray. The tray notice is proven through the real main.js functions under node, and the daemon side through the real app factory.
- **Phone duplicates.** A scheduled workflow parked on a question sends the phone two lines: the answerable question and the schedule's "needs you" line.
- **Tray timing.** A run that fails in the last 25 seconds or so before a window opens gets no OS notice. It does appear in the bell's "While you were away".
- **Two notices from a browser tab.** A browser dashboard tab with notifications allowed, beside the desktop app in the tray, announces an outcome twice.
- **Different wording by destination.** The phone line keeps "FAILED" (pinned since v1.119). The desktop notice says "could not finish".

---

## 0d. Final re-audit (v1.359.0, 2026-10-19)

**What was re-checked, and how.** Nine build waves (v1.351.0 to v1.359.0) have now shipped 83 of the 86 gaps in section 9. The owner chose not to build G83, G85 and G86. Waves 8 and 9 (sections 0b and 0c) worked through the 14 regressions and 8 partly met gaps that the v1.357.0 re-audit (section 0) found.

Seven re-audit lanes then re-marked all 213 checklist items against the code at HEAD `02bd7c83` (v1.359.0). The lanes used the same groups as before: A-B, C-D-O, E-J, F-K, G-N, H-I-M and L. Each lane:
- re-read the code behind every item and every gap in its sections, and checked each gap against its acceptance;
- ran the pinned tests for its areas one file at a time. All were green: for example 289 pytest and 358 vitest tests for A-B, and 234 pytest and 233 vitest tests for G-N;
- sent read-only GET requests to the running app. It still reports v1.354.0, so live checks cover waves 1 to 4 only;
- ran read-only probes where reading the code was not enough. Lanes probed the Connect-door matcher, the tool selector (side by side with v1.350.2) and the failure-words classifier.

This synthesis then checked the lanes' surprising and contested claims directly in the tree. Every one held:
- **The two desktop notices for one failed schedule.** Read in `NotificationBell.tsx`, `platform.py` and `comm/integrations.py`.
- **The false Connect doors and the Outlook case.** Re-run through `connect_offers` at HEAD.
- **The Guide rule firing on unrelated how-to questions.** Re-run through `select_auto_tools`.
- **Seven claims read at the lines the lanes cited:** the "reads anywhere" words, the grey helper-run verdict, the armed delete press, the Status map's "tool packs", the custom endpoint's missing label (`chat_turn.py:1453-1455`), the import placeholder (`addPackWords.ts:49-53`) and Help's stale catch-all.

Where lanes proposed different scores for a category, I used the median, rounded down, as in section 0. Lanes disagreed about three gaps: G49, G61 and G76. For each I kept the stricter verdict, because the code supports it (see 0d.4).

### 0d.1 Overall score: **74 / 100** (55 at the first audit, 70 at the v1.357.0 re-audit)

The twelve categories now average 75.1. I score 74, one point lower, for two reasons:
- **Several of the larger pieces have never run against the real service:** Microsoft 365, GitHub's remote server, the Gemini key, the task door between two PCs, `rest_call`, LM Studio and Ollama.
- **The newest waves are not on the user's install.** It runs v1.354.0. v1.355.0 to v1.358.0 are published. v1.359.0 is pushed, and its Tests and Release runs were still going when this was written.

152 of 213 items now pass, up from 41 at the first audit and 130 at v1.357.0. None fail. 79 of the 83 built gaps meet their acceptance in the code.

The problems section 0 named are fixed in the code:
- The Schedules row leads with Needs you, Part failed or Failed, and a scheduled workflow parked on a question reads Needs you.
- Web research that names an app reaches the web again.
- Chat picks the 24 tools of an app that fit the message, so GitHub's pull-request tools stay in reach.
- A failure names its provider ("Claude Code didn't answer in time").
- One helper run shows one receipt, with a plain step-by-step log.
- The desktop shell says "Iron Jarvis".
- The Directory's built-ins and its "N connected" count agree with its cards.
- Emergency stop also stops a scheduled workflow that is waiting on a question.

What holds the score at 74 is a shorter list of narrower problems:
- **Background failures.** A failed unattended run raises no OS notification while the app sits in the tray. While a window is open, it raises two.
- **The user's own machine is never named.** A reply from their model server says "local", never which machine.
- **False Connect offers.** The new Connect door offers to connect Stripe for "stripe the table rows" and Postgres for a question about Postgres. It offers Gmail to an Outlook user.
- **Raw failure text.** A failure the word table does not recognise still reads as raw CLI text on the run page, the Schedules row and the bell.
- **Terminal sign-in steps.** The Connections Rescan note and Jarvis's own health answers still tell a signed-out user to open a terminal.
- **Setup still needed.**
  - Gmail and Google Drive still need a Google developer setup.
  - A user with no key and no CLI still needs Ollama and a model download of about 2 GB (G86, the owner's call).

### 0d.2 Category scores

| Category | First audit | v1.357.0 | Now | Why |
|---|---|---|---|---|
| Action clarity | 60 | 77 | 83 | Schedules, missions, helper runs, the work line and the browser sidebar lead with the ledger's verdict from one status table, and a helper run has one receipt. The chat line still names the model but not the harness or, for the user's own model server, the machine. @-agent replies say only "from X · time", and a Build pane can only say "take a look". One failed schedule raises two notices in different words. |
| Execution visibility | 62 | 76 | 78 | Running now, the bell's rule and replay, the waiting-chat notice and the step-by-step log all work. Three cases are still silent: a failed unattended run while the app is in the tray, a chat ask while the user is on another page of the same window, and a helper run that ends after the user left its chat (unread dot only). |
| Connections | 42 | 58 | 66 | Fixed since v1.357.0: GitHub's tool ranking, the Directory's built-ins and count, Microsoft 365's sign-in state, key checks on save, LM Studio and llama.cpp detection, and a Jarvis-to-Jarvis setting. Still open:<br>• Gmail and Drive need a Google developer setup.<br>• Slack is two unrelated things.<br>• There is no single Connected view, and two model-server forms remain.<br>• Continue follows only a token saved in the chat's own card.<br>• Phone chat cannot use a connected app it is told about.<br>• The new Connect door misfires. |
| Import/discovery | 45 | 74 | 76 | The scanner, the off and already-here states, vaulted secrets and an outcome that names the real Tools menu all hold, and local model servers are detected. Nothing appears at first run. Every server outside the catalog reads only "A tool from <app>". |
| First-run experience | 52 | 70 | 74 | The release page explains the download, the empty chat shows the machine check, the confirmation uses the pressed words and the demo says what it is. Every local model server is one press. Still missing: a path for a user with no key or CLI (G86). Sign-in and the model download happen in a terminal pane, most apps need developer credentials, and the Rescan note says to open a terminal. |
| Error/recovery UX | 60 | 70 | 75 | Chat, the Build pane, banners, the bell, Status and Schedules give a plain headline, folded detail, a fix and a "not affected" line. Recovery is announced. Four things still fall short:<br>• Unrecognised failures read raw outside chat.<br>• The terminal sign-in steps survive in three places.<br>• Pack fixes point at a "Connected packs" section that does not exist.<br>• Help's catch-all points at a removed card. |
| Consistency | 40 | 60 | 68 | One status table, one approval vocabulary, one helper-run receipt and calm badges are real. Still inconsistent:<br>• Receipts are separate components.<br>• The Session board files stopped runs under red "Failed".<br>• 73 files keep boxed red notes.<br>• Asks come in four layouts.<br>• One model is "Your model server" in the menu and "fleet" on the receipt.<br>• One schedule failure reads "could not finish", "FAILED" and "Failed".<br>• The chat line reads "Ran history_search" where the run log says "Searched your chats". |
| Discoverability | 50 | 63 | 65 | Everything is tiered, phone chat and self-care can be found, how-do-I questions reach the Guide, and naming an unconnected app leaves its Connect door. Chat never suggests a mission. "what are missions", "what is build for" and "what apps can you work with?" arm nothing, and "summarize my latest emails" reaches for web search. |
| Beginner friendliness | 45 | 63 | 69 | "Daemon" is gone from the desktop shell, and model rows, Connections and Secrets read in words. Still in jargon: the chat preflight prints "claude-cli" with a terminal recipe, the Status map says "tool packs" and "panes", extension failures say "the pack", and the nav keeps Sentinels, Reflexes and Artifacts. |
| Trust | 55 | 70 | 76 | Emergency stop stops self-started work, including parked workflows. No model is told it runs locally. Reading is disclosed and can be limited, and every ask says who, why and whether it can be undone. Gaps: the run page and mission screen never say local or cloud, and the user's own model server is never named. The approval chip says "reads anywhere" after reading is limited. |
| Stability | 85 | 92 | 93 | All 14 stability items pass. Small gaps: an @-agent round has no Stop, and a failed unattended run raises no OS toast while the app is in the tray. |
| Marketplace/capability readiness | 58 | 76 | 78 | One catalog and one add/remove door, with permissions, On/Off, repair, a sign-in state and key checks carried as data. Models, agents, channels, web services and local servers keep their own row shapes. A refused key reads green again after a restart, because key verdicts live in memory. Intent matching is by name only, works only on the chat page, and now shows false Connect doors. |

Where lanes disagreed (median, rounded down):
- **Connections:** 70 from C-D-O and 62 from F-K. F-K judged from the chat side and counted the phone lane and the Continue limits.
- **Error/recovery:** between 72 and 78 across five lanes.
- **Execution visibility:** between 78 and 80.
- **Consistency:** between 67 and 70.
- **Action clarity:** 82 and 84.
- **Beginner friendliness:** 68 and 70.
- **Discoverability:** 65 and 66.

The other five categories were scored by one lane each.

### 0d.3 PASS / PARTIAL / FAIL (first audit → v1.357.0 → now)

| Section | Items | PASS | PARTIAL | FAIL |
|---|---|---|---|---|
| A. Actions self-evident (incl. 12 surfaces) | 33 | 3 → 13 → 24 | 30 → 20 → 9 | 0 → 0 → 0 |
| B. Execution receipt | 24 | 10 → 22 → 23 | 14 → 2 → 1 | 0 → 0 → 0 |
| C. Connections | 21 | 2 → 10 → 12 | 17 → 11 → 9 | 2 → 0 → 0 |
| D. Import | 16 | 1 → 13 → 14 | 15 → 3 → 2 | 0 → 0 → 0 |
| E. First run | 11 | 1 → 5 → 6 | 10 → 6 → 5 | 0 → 0 → 0 |
| F. Natural-language discovery | 6 | 0 → 0 → 1 | 6 → 6 → 5 | 0 → 0 → 0 |
| G. Error & recovery | 10 | 4 → 7 → 8 | 6 → 3 → 2 | 0 → 0 → 0 |
| H. Consistency | 15 | 2 → 9 → 9 | 13 → 6 → 6 | 0 → 0 → 0 |
| I. Engine room | 14 | 2 → 10 → 12 | 11 → 4 → 2 | 1 → 0 → 0 |
| J. Discoverability | 5 | 0 → 2 → 2 | 5 → 3 → 3 | 0 → 0 → 0 |
| K. Trust | 11 | 1 → 5 → 8 | 10 → 6 → 3 | 0 → 0 → 0 |
| L. "Did it work?" | 14 | 2 → 9 → 8 | 12 → 5 → 6 | 0 → 0 → 0 |
| M. "What does this mean?" | 8 | 0 → 3 → 3 | 8 → 5 → 5 | 0 → 0 → 0 |
| N. Stability | 14 | 11 → 14 → 14 | 3 → 0 → 0 | 0 → 0 → 0 |
| O. Marketplace readiness | 11 | 2 → 8 → 8 | 9 → 3 → 3 | 0 → 0 → 0 |
| **Total** | **213** | **41 → 130 → 152** | **169 → 83 → 61** | **3 → 0 → 0** |

Notes on the counts:
- **Source.** Every count comes from the lanes' item-by-item marks. No lane missed an item, so no old status was carried forward, and each lane's summary agrees with its own marks.
- **L has one PASS fewer than at v1.357.0 (9 → 8).** The v1.357.0 lanes' item marks are not kept in this report, so the item that moved cannot be named. Each of L's six PARTIAL marks points to a gap that is in the code today:
  - Build panes say only "take a look".
  - Rarer built-in tools show raw ids.
  - Approving an agent-browser step resumes nothing until the agent tries again.
  - A remote task has no end state.
  - Nothing reaches the user from the tray.
  - Rarer approval asks use raw ids.

  None of them is a fix that was undone. The closest to a step back is the grey helper-run verdict (regression 5).
- **H, J, M, N and O** have the same counts as at v1.357.0.
- **One cause, three items.** A "Which machine/node", B "Machine/node" and K "Which machine" are all PARTIAL because `route_label` names only `fleet-*` providers (`chat_turn.py:1453-1455`).
- **B "Harness" passes while A "Which agent/harness" is PARTIAL**, as before. The harness is recorded and named on the run page, but never in words on the chat line.

### 0d.4 Gaps whose acceptance is not fully met

79 of the 83 built gaps meet their acceptance. These 4 are partly met:

| Gap | What works now | What remains |
|---|---|---|
| G21 Replies say where they ran | A fleet node's reply says "on <label>", Claude's says "· cloud", and the menu badges say "on your plan" / "pay per use". | `route_label` names only `fleet-*` providers (`chat_turn.py:1453-1455`). The user's default route is custom/fleet, so a reply from their DGX box reads "answered by fleet · local", never "on DGX Spark", although the mission screen and run page name the same box. The run page never says local or cloud. |
| G49 Import explains itself | The sources door comes first, the command sits under Details, and the outcome lists tool names and names Tools › Connections. | A server outside the catalog reads only "A tool from <app>" (`lib/addPackWords.ts:49-53`). Live, all six servers found on this PC read that way. Lane H-I-M called it met; lane C-D-O called it partly met, because the acceptance asks for a one-line description of each found tool. The stricter verdict stands. |
| G61 Remote Jarvis node | A token-guarded task door, one low-trust run per task and one reply home from every ending. A "reach this Iron Jarvis from my network" setting is applied at every start, and the register form picks the task API itself. Tested with two real app factories on one machine. | Never run on two physical PCs, which is what the acceptance names. A Windows Firewall block of the newly listening app is neither detected nor explained. Both PCs need the setting on and a restart before a reply can come home. Section 0b counted it "met in code"; two lanes here call it partly met. |
| G76 One vocabulary | `VOCABULARY.md` is the canon. One scan covers every dashboard string, and a second covers `main.js`, the schema and a fixed list of daemon files. | The backend scan is a fixed file list, so daemon sentences outside it still reach users:<br>• the Status map's "4 of 4 tool packs running." and "0 open panes" (`system_map.py:258, 701-703`; live on v1.354.0);<br>• extension failure reasons such as "the pack's server couldn't be reached", whose fix points at "Tools → Connected packs" (`mcp/tools.py:767, 784`), while the section is titled "Connected extensions";<br>• Directory memory cards: "A memory brain…" (`connectors/service.py:717-752`);<br>• "plug-in tools" (`routes/agents.py:4203-4206`).<br>Section 0b counted it met. |

Met, with remainders worth knowing:
- **G03 / G05 (the bell and waiting chats).** A failed unattended run raises no OS toast while the app is in the tray (`comm/channels.py:255-270`). A chat waiting for an OK while the user is on another page of the same window is listed nowhere but the sidebar's dot (`routes/chat.py:1042-1046`).
- **G15 (connect in chat and continue).** Continue appears only for a token saved in the chat's own card. It does not appear for a Pixio key, or for an app connected in Settings.
- **G22 / G24 (tool and approval words).** About 40 built-ins have words on the chat line and approval card. The rest read "Ran guide_search" and "run assign_work" (`lib/workTarget.ts:56-107`; `lib/approvalWords.ts:69-71`).
- **G28 (failures in plain words).** Failures the classifier does not recognise fall back to raw text outside chat (`failure_words.py:584-597`).
- **G33 (sign in from Accounts).** The Accounts rows have Sign in. The same card's Rescan note still prints the terminal recipe in a green success note (`routes/connections.py:1031`).
- **G39 (chat knows apps by name).** Arming happens only on the chat page (the stream lane) and only by app name. The phone lane is told an app is connected but gets none of its tools.
- **G41 (refusals leave a fix button).** The Connect door misfires (regressions 2 and 3).
- **G81 (limit which folders Jarvis reads).** The approval words still say "reads anywhere" after reading is limited (regression 4).

### 0d.5 Regressions found

1. **Two desktop notices for one failed or needs-you scheduled run while a window is open** (v1.351.0, G03). Confirmed by reading the code; not reproduced live.
   - The bell's unattended rule pings "Your schedule X could not finish" (`NotificationBell.tsx:298-336, 1879-1888`).
   - The schedule's own This PC delivery also toasts "Schedule “X”: FAILED — The run failed: …" (`platform.py:1821-1842`; `comm/integrations.py:174-177`; DesktopNotifyBridge).
   - A failed scheduled workflow also rings two bell rows (`NotificationBell.tsx:407-427`).
2. **The v1.359.0 Connect door misreads everyday and research sentences** (G41). Confirmed by running `connect_offers` at HEAD:
   - "stripe the table rows" offers "Connect Stripe in Settings";
   - "explain how postgres indexes work" offers Postgres;
   - "the sentry stood guard at the gate in my story" offers Sentry.

   Before v1.359.0 these only armed `app_connect`, which the model could ignore. Causes: `app_names.py:130-141` (`_NOT_THE_APP` does not cover these words), `:249` (`_HOW_TO_RX` needs "how to/do/does"), and `:316-359` (`_for_own_data` defaults to the user's own data).
3. **An Outlook user is offered Gmail.** With Microsoft 365 connected and Gmail not, "check my inbox" and "what's in my email today" put "Connect Gmail in Settings" under the reply. The apps section tells the model "Gmail: not connected", and Microsoft 365's tools are not armed. Confirmed by probe. The matcher is older (`app_names.py:101-110`); the visible door is new in v1.359.0.
4. **"Reads files anywhere on this PC without asking" after reading is limited** (v1.356.0, G81). `READS_LINE` (`lib/permissionLevels.ts:34`) is in all three level descriptions. The chat's approval chip shows it, and so does the Access summary's Approval level row, directly under a Reading files row that says Jarvis reads only the chosen folders.
5. **Plausible: a helper run's verdict lost its colour and can be cut off** (v1.358.0, one receipt). Not checked at phone width.
   - The paired reply drops the card's red "Failed" or amber "Part failed" headline (`RunResultCard.tsx:416-470`).
   - The verdict is now the last words of the settled row's grey span, which truncates and has no title (`AgentRunRow.tsx:121-137, 394-402`). A narrow window cuts "· failed" first.
   - The agent's own chat line still says a failure in words.
6. **Minor: the Guide rule fires on unrelated how-to questions** (v1.353.0, G38). "how do I connect my printer to wifi", "how do I send a wire transfer" and "how do I install node" now arm `guide_search`, `app_search` and `app_status`. At v1.350.2 they armed nothing. Not run against a live model.
7. **Minor, carried since v1.356.0: the armed "Clean up now?" press on Sessions is still grey.** Its caller forces `!text-zinc-400` (`app/sessions/page.tsx:340-346`). Wave 8 fixed only the "Clear finished…" button beside it.
8. **Minor, deliberate (v1.359.0): one model, two names.** The model menu calls the user's own server "Your model server" (`lib/onboarding.ts:313-322`). The reply, the helper row and card, and the run page call the same pick "fleet" (`lib/answeredModel.ts:33-46`).

None of the waves added boxed cards or literal colours to the calm chat. The new pieces are plain lines and folds in theme tone tokens.

### 0d.6 Engine-room leaks remaining

On paths a typical user reaches:

| Where | What the user sees |
|---|---|
| `providers/cli_auth.py:50-58` (SIGN_IN_FIX). Shown after Rescan on Settings › Connections, inside a green success note (`routes/connections.py:1031`; `ConnectionsPage.tsx:2164-2173`). Also on the welcome card's Optional extras (`onboarding/doctor.py:742-747`), in Jarvis's own health answers and in the use-model refusal (`routes/settings.py:96`). | "Claude Code isn't signed in. Open a terminal, run `claude`, then `/login`, then click Test on Connections." |
| `components/chat/PreflightNote.tsx:50-68` (above the composer); `components/agents/mission/MissionComposer.tsx:41-80` | "claude-cli is installed but not signed in… Run `claude` in a terminal, then /login"; "Your default model, fleet-custom, isn't reachable right now." |
| `providers/failure_words.py:584-597` (raw fallback) on the run page, Schedules row and bell | "The run failed: codex-cli: CLI exited 1: error: unexpected status 400 Bad Request: model is not supported" |
| `lib/workTarget.ts:56-107`; `lib/stepWords.ts:184` (chat work line and receipt) | "Ran guide_search", "Ran remember_preference", "Ran history_search", counted as "ran 1 tool". The run log words the same tools ("Searched your chats"). |
| `lib/approvalWords.ts:69-71`; `components/chat/ApprovalCard.tsx:241-244`; `desktop/main.js:2871-2872` | "The assistant wants to run pack_restart", "run assign_work"; the desktop toast says "use pack restart" |
| `daemon/routes/system_map.py:258, 701-703` (Everything › Status; live on v1.354.0) | "4 of 4 tool packs running.", "0 open panes" |
| `mcp/tools.py:705-790` (Directory card and Tools row reasons) | "the pack's server couldn't be reached", "…on Tools → Connected packs, then press Retry." The section is titled "Connected extensions". |
| `connectors/service.py:717-752` (Directory memory cards) | "A memory brain served by a tool you added.", "An external RAG service.", "A custom memory source." |
| `app/help/page.tsx:560-575` (Help › Something else?) | "The System health card on the Overview (switch on Advanced at the bottom of the nav)". Neither exists any more. |
| `components/settings/pages/ToolsPage.tsx:1672-1675` | "Extensions add bigger abilities — files, repositories, memory", while the Directory lists files as built in |
| `lib/nav.ts:249, 284, 415` | "Sentinels", "Reflexes", "Artifacts" |
| `lib/answeredModel.ts:33-46`, run page (the user's own endpoint) | "answered by fleet", "Ran on fleet", "DGX Spark · fleet", where the menu says "Your model server" |
| `daemon/routes/agents.py:4203-4206` | "…so autonomous agents may use plug-in tools without asking" |
| `app/schedules/page.tsx:1040-1046`; `app/sessions/page.tsx:288` | "open session"; "No orphaned worktrees to prune." |

Advanced or rare paths:

| Where | What the user sees |
|---|---|
| `lib/addPackWords.ts:41` | "HTTP server" (the third add door) |
| `components/agents/SetupCard.tsx:1879-1881, 2377-2379` | "http-task (task API)", "openai-chat (chat/completions)" for an address that is not a task door |
| `connections/RestHookups.tsx:181-185` | A raw "Config (JSON)" box |
| `connections/specs.py:254, 272`; `ConnectionsPage.tsx:1056-1059` | Google Cloud / Entra OAuth client steps; "<provider>_oauth_client_id" |
| `components/memory/baseCatalog.ts:96-99`; `settings/pages/LegacySettingsForm.tsx:569-575` | "An MCP memory server … Claude-Desktop-style config"; "MCP integrations" |
| `connections/EndpointModelPicker.tsx:65` | "Server type: OpenAI-compatible / Anthropic-compatible", always shown |
| `daemon/routes/settings.py:936` | "ollama pull llama3.2" |
| `mcp/tools.py:752-755` | "Restart Iron Jarvis from a terminal that can run npx…" |
| `computeruse/tools.py:271-277`; `settings/pages/BrowserPage.tsx:240` | "Pending approval id=<id>."; "run ad-hoc" |
| `app/terminals/page.tsx:1072, 1375`; `MoodOrb.tsx:58-60, 205`; `StatusOverview.tsx:308` | "Attaching to sessions…", "this terminal has no cwd", "Thinking — a session is in flight", "· stream offline" |
| `comm/channels.py:289`; `desktop/main.js:3962` | "(daemon not up)"; "Iron Jarvis — dashboard did not start" |
| `TurnReceipt.tsx:1086-1090` (expanded receipt) | "(default)", "(prompted-tools)", "auto-tier", "context 43%". Acceptable as Level 2 detail. |

Until it updates, the user's install (v1.354.0) also still shows the Status-map words fixed in v1.356.0: "Ready: anthropic, claude-cli, codex-cli, custom".

### 0d.7 Not built, by the owner's decision

- **G83, curated directory feed.** `connectors/catalog.py` is still the only source of Directory cards. Because there is one catalog, a feed added later would only add rows to it.
- **G85, desktop-control pack.** Jarvis cannot click around other desktop apps. Settings says so plainly ("Your browser add-on and the agent browser."), so nothing over-promises.
- **G86, bundled local model.** A user with no API key and no signed-in CLI still has to install Ollama and download a starter model of about 2 GB before the first real answer. The one-press Ollama and the download button shorten that path, but not to five minutes. This is now the largest single gap to the checklist's five-minute first run.

### 0d.8 Never proven live

- **Microsoft 365.** Nobody has signed in to Microsoft through it. The tests use a stand-in that lists the real server's tool names (`tests/test_microsoft_365_v13560.py`).
- **Another Jarvis (the task door and the network setting).** Tested with two real app factories on one machine. Never tried on two physical PCs, and the Windows Firewall prompt on the first network listen is unverified.
- **`rest_call`.** Never run against a real CRM or any real service. It takes bearer keys only.
- **GitHub's maintained remote server.** Never connected live. The tool ranking was tested with the tool names from GitHub's README.
- **Gemini API key.** There is no key on this PC, so Google has never answered through it.
- **Ollama, LM Studio and llama.cpp.** None is installed here. The one-press use, the detection and the starter-model download are verified in code and tests only.
- **Slack.** `@zencoderai/slack-mcp-server` 0.0.1 has never been run against Slack.
- **Smaller pieces.** The mission cost note has never been seen against a real priced mission, the context refit has never triggered live, and Open folder (a POST) was not exercised live.
- **Waves 5 to 9 overall.** v1.355.0 to v1.359.0 were judged from code and tests. The running app is v1.354.0, and the newest published release is v1.358.0. v1.359.0 is pushed (`02bd7c83`), and its Tests and Release runs were in progress when this was written.

### 0d.9 Verdict

Iron Jarvis is now a product a general user can live in day to day. The first audit's worst problems are gone from the code: false "ok" and "Done", silent unattended failures, green badges on broken packs, "daemon" on the screen, raw tool ids in chat, two receipts for one run, and an Emergency stop that did not stop. Schedules, missions, helper runs, the bell, the Directory and the error lines now say what chat says. 152 of 213 items pass, none fail, and 79 of the 83 built gaps meet their acceptance.

It is not finished. 61 items are still PARTIAL. The problems left are narrower, but users will meet them:
- failed background work that is silent in the tray and doubled while a window is open;
- Connect doors offered for everyday words, and Gmail offered to an Outlook user;
- a reply from their own model server that never names the machine;
- raw CLI text for failures the word table does not know;
- terminal sign-in instructions after Rescan;
- "reads anywhere" shown after reading is limited.

The most valuable next wave would fix those six, put the custom endpoint's label on the receipt, and widen the backend vocabulary scan to every user-facing daemon sentence.

After that, the largest remaining steps are not code. One is proving Microsoft 365, GitHub's server, the Gemini key and the two-PC task door against the real thing. The other is the owner's decision on G86. Without a bundled model, someone with no key and no CLI cannot reach a real answer in five minutes.

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