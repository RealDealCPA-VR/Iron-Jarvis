# Record of the CLAUDE.md split (2026-11-14)

This file is the record of what was moved where. It is written by
`scripts/split_agent_rules.py` and is not edited by hand.

## The source

- File: the root `CLAUDE.md` as it stood before the split.
- Lines: 19951.
- Size: 1,429,770 bytes (LF line ends).
- SHA-256 (LF line ends): `ce78e78c5de29bd4f19da1a23025d679e542f45877e7482fad13956a001f5738`.
- Kept unchanged as `docs/rules/ARCHIVE-2026-11-14.md`.

## Totals

- Pieces: 372 (364 top-level bullets, 8 other parts).
- Joined in order, the pieces are the source file exactly (checked at split time).
- Each bullet went, unchanged, to exactly one area file. "Unchanged" is checked by
  the hash in the table below: the text between a bullet's first line and its last
  non-blank line.

| Area file | Bullets | Other parts |
|---|---:|---:|
| `chat.md` | 49 | 0 |
| `context.md` | 7 | 0 |
| `agents.md` | 20 | 0 |
| `providers.md` | 26 | 0 |
| `build.md` | 94 | 0 |
| `tools.md` | 18 | 0 |
| `settings.md` | 8 | 0 |
| `connections.md` | 8 | 0 |
| `browser.md` | 19 | 0 |
| `documents.md` | 10 | 0 |
| `memory.md` | 15 | 0 |
| `automations.md` | 4 | 0 |
| `messaging.md` | 5 | 0 |
| `daemon.md` | 13 | 0 |
| `dashboard.md` | 18 | 0 |
| `desktop.md` | 10 | 0 |
| `ci.md` | 17 | 2 |
| `cross-area.md` | 23 | 0 |
| root `CLAUDE.md` (rewritten short; the old text is only in the archive) | 0 | 6 |

## Parts that are not bullets

| Old lines | Section | Where it went | SHA-256 (first 12) |
|---|---|---|---|
| 1-6 | (title and introduction) | rewritten in the root `CLAUDE.md`; the old text is in the archive | `e6c6bee6788e` |
| 7-22 | The three processes | rewritten in the root `CLAUDE.md`; the old text is in the archive | `87b55d984e3d` |
| 23-57 | Commands | `ci.md`: the body unchanged, its `##` heading line turned into a `###` heading | `68e907115acf` |
| 58-130 | Release flow (how the user receives your work) | `ci.md`: the body unchanged, its `##` heading line turned into a `###` heading | `4e4f42907df1` |
| 131-132 | Hard rules (each one was learned the expensive way) | rewritten in the root `CLAUDE.md`; the old text is in the archive | `111ec6501a30` |
| 18902-18903 | Map (where things live) | rewritten in the root `CLAUDE.md`; the old text is in the archive | `6cd364574716` |
| 19227-19238 | Verifying against the LIVE app (the user's running install) | rewritten in the root `CLAUDE.md`; the old text is in the archive | `d138c329a4d0` |
| 19239-19248 | Direction (the product thesis) | rewritten in the root `CLAUDE.md`; the old text is in the archive | `3e28ac00c964` |

## Every bullet

"Also listed in" means the bullet's headline is listed, as a pointer only, in
that area file's "Also touches this area" list.

| # | Old lines | Went to | Also listed in | SHA-256 (first 12) | Headline |
|---:|---|---|---|---|---|
| 1 | 133-141 | `memory.md` | `chat.md` | `77fb175e4a36` | The identity spine reaches EVERY prompt seam (v1.144.0) |
| 2 | 142-150 | `context.md` | - | `e6d2333db602` | History is BUDGETED, never sliced (v1.146.0) |
| 3 | 151-168 | `context.md` | - | `97cd6a4d9cc7` | Compaction is MODEL-written and LEDGER-checked (v1.153.0) |
| 4 | 169-176 | `context.md` | - | `61d985c69d53` | An assistant turn and its `role="tool"` results are ONE unit (v1.152.0) |
| 5 | 177-192 | `context.md` | - | `08be3cfcde08` | Keep big results OUT of the context, don't trim them after (v1.159.0) |
| 6 | 193-218 | `chat.md` | - | `c3b62806d97e` | Every chat reply is ACCOUNTABLE where the user is standing (v1.165.0) |
| 7 | 219-234 | `messaging.md` | - | `4b5d4716648d` | A remote agent is EDITABLE, and an edit must never eat its credential (v1.164.0) |
| 8 | 235-266 | `chat.md` | - | `3fc4c1a56b95` | A draft the user will SEND is fenced, boxed, and copied as RICH TEXT (v1.161.0) |
| 9 | 267-289 | `context.md` | - | `8f85bf3c8987` | The REPL's writes are CONFINED; its reads are not (v1.160.0) |
| 10 | 290-338 | `daemon.md` | `tools.md` | `380d580096c9` | NOTHING BLOCKING RUNS ON THE EVENT LOOP (v1.153.1) |
| 11 | 339-358 | `agents.md` | - | `7032d6afd451` | Every door ends in `create_session`, so the folder and the ask live THERE (v1.231.0) |
| 12 | 359-377 | `automations.md` | - | `67fd269ebcee` | A step that RAISES is a failed step, and a result is written when it LANDS (v1.231.0) |
| 13 | 378-398 | `automations.md` | - | `df55f00e5d5d` | A cancel goes to the task's OWN loop, and a fire that did not happen is written where the user reads (v1.231.0) |
| 14 | 399-415 | `messaging.md` | - | `6adb3526bee0` | A refused credential is not an empty batch, and at-most-once must leave a trace (v1.231.0) |
| 15 | 416-434 | `memory.md` | - | `55bc404124b8` | A rule that lives in one page's JSX is not a rule, and a delete must untag what SPAWNS (v1.220.0) |
| 16 | 435-451 | `tools.md` | - | `a95fa1bec324` | The roster is the CONTRACT, and the gate is in the registry (v1.227.0) |
| 17 | 452-480 | `chat.md` | - | `74f58b437405` | Model output is ALMOST JSON; recover it in ONE place (v1.225.0) |
| 18 | 481-503 | `daemon.md` | - | `8ebf4c2d9d7f` | Armed is not healthy: a loop reports its OWN cycles (v1.229.0) |
| 19 | 504-517 | `daemon.md` | - | `7bd4d6b4694f` | A log handler on the app tree runs inside every `except` branch the daemon has (v1.229.0) |
| 20 | 518-528 | `ci.md` | - | `a36070188679` | Shipping the mechanism is not shipping the feature (v1.218.0) |
| 21 | 529-548 | `build.md` | - | `bf855500823c` | An injected value must be injected, and a field must have a way IN (v1.217.0) |
| 22 | 549-560 | `build.md` | - | `5d0e51107d13` | One PTY, many attaches: only the PRIMARY attach resizes it (v1.232.0) |
| 23 | 561-586 | `build.md` | - | `702981dcc71a` | A Build terminal OUTLIVES the Build page (v1.243.0) |
| 24 | 587-620 | `chat.md` | `documents.md` | `4cf241b7ceb9` | A chat handed a file gets a FOLDER, project or not (v1.244.0) |
| 25 | 621-641 | `build.md` | - | `4bd36d87bbba` | Build daily-driver pass, B1 (v1.245.0) |
| 26 | 642-667 | `chat.md` | - | `eef8ecba3672` | A chat turn never goes quiet, never ends empty (v1.246.0) |
| 27 | 668-691 | `tools.md` | - | `05574f1909eb` | An attended ask WAITS; a batch is ONE ask; office work stays in chat (v1.247.0) |
| 28 | 692-732 | `build.md` | - | `31bed4a00ced` | The Build terminal path is BYTES, PUSHED and paced; only the pane on screen draws (v1.248.0) |
| 29 | 733-862 | `desktop.md` | `daemon.md`, `build.md` | `11bcd2229e2e` | An update stops tidily, and what it interrupted is offered back (v1.249.0) |
| 30 | 863-876 | `daemon.md` | - | `7c5cc510d302` | A backup that never leaves the disk it protects is one failure from nothing (v1.249.0) |
| 31 | 877-900 | `chat.md` | - | `5197c738110f` | Cost scales with attention, and the composer is not the page (v1.250.0) |
| 32 | 901-927 | `daemon.md` | - | `ee4503b2f4e1` | A warm-up without single-flight is duplicated work, and a boot must be able to explain itself (v1.251.0) |
| 33 | 928-942 | `tools.md` | - | `5f436ce9f0ff` | A grant is written where the NEXT run reads, and yolo never rides an escalation (v1.232.0) |
| 34 | 943-947 | `desktop.md` | - | `acc81afb75c3` | Frozen-build verification |
| 35 | 948-953 | `agents.md` | - | `950a367adf76` | `GET /sessions/{id}` returns `{session, transcript}` — NESTED. |
| 36 | 954-968 | `documents.md` | - | `da581150d5e9` | PDF redaction edits the page; it never regenerates it (v1.154.0) |
| 37 | 969-981 | `documents.md` | - | `d21443ba98ef` | A tool that writes a file says WHERE, absolutely (v1.153.2) |
| 38 | 982-1000 | `providers.md` | - | `0ff388d85ed0` | Never let a real-provider failure return mock output (v1.162.0) |
| 39 | 1001-1038 | `providers.md` | `chat.md` | `3f746120a1c2` | "It answered, so it is up" is false for a proxy (v1.228.0) |
| 40 | 1039-1044 | `providers.md` | - | `c3a322a39e36` | OpenAI ChatGPT-account backend retires model ids |
| 41 | 1045-1048 | `providers.md` | - | `5f72ac0ae8f4` | One-shot agent utilities |
| 42 | 1049-1053 | `daemon.md` | - | `37e81c38b4d9` | Event payloads (v1.237.0) |
| 43 | 1054-1057 | `ci.md` | - | `bd640eb00921` | Parallel agent work |
| 44 | 1058-1116 | `ci.md` | - | `2f1e741962cf` | A frontend `waitFor` must wait for the THING YOU ARE ASSERTING (v1.177.1) |
| 45 | 1117-1123 | `ci.md` | - | `4142dd0ac869` | A PACKAGE THAT RE-EXPORTS A FUNCTION SHADOWS THE MODULE OF THE SAME NAME (v1.254.0) |
| 46 | 1124-1134 | `tools.md` | - | `e7271618edec` | A DESTRUCTIVE BULK ACTION MOVES, IT DOES NOT DELETE (v1.256.0) |
| 47 | 1135-1150 | `tools.md` | - | `7fcf19a0e288` | A folder is WRITABLE when a file lands in it, and `tempfile` is not the way to find out (v1.228.0) |
| 48 | 1151-1165 | `dashboard.md` | - | `20ee908d3ef8` | Freshness is the SERVER's header, and a caller's abort is not an outage (v1.230.0) |
| 49 | 1166-1188 | `dashboard.md` | - | `b56d35cb178d` | Cost scales with attention, and a conditional GET is only as good as the header the browser may READ (v1.230.0) |
| 50 | 1189-1202 | `providers.md` | - | `aa3070f8b096` | "Connected" is the ROUTER's answer, and a status row that reads its own store is a second truth (v1.230.0) |
| 51 | 1203-1214 | `ci.md` | - | `7ee3cd662cfc` | A SOURCE PIN READS A FILE THE CI RUNNER CHECKED OUT WITH CRLF (v1.232.1) |
| 52 | 1215-1217 | `ci.md` | - | `b4dfea4234be` | Windows dev shell |
| 53 | 1218-1227 | `daemon.md` | - | `49803063763b` | Before deferring a boot step into the daemon lifespan, ask who builds a platform WITHOUT one. (v1.257.0) |
| 54 | 1228-1243 | `ci.md` | - | `548f26a0d075` | A pin that survives the obvious revert is UNCHECKED — mutate what the pin itself claims. (v1.257.0) |
| 55 | 1244-1255 | `ci.md` | - | `0a61cb381296` | Edit N tests, run all N — verify the FILE, not the one test you were thinking about. (v1.257.0) |
| 56 | 1256-1270 | `ci.md` | - | `806a2d94525b` | To falsify a WAIT, mutate it by DELETING it — shrinking it to `sleep(0)` proves nothing. (v1.257.0) |
| 57 | 1271-1285 | `dashboard.md` | - | `f86ac01f2b16` | A component is not its own chunk: before deferring one, check what else its module exports as a VALUE. (v1.258.0) |
| 58 | 1286-1295 | `ci.md` | - | `8023febb3164` | Inserting a declaration above a named one ORPHANS its doc comment — check for one first. (v1.257.0) |
| 59 | 1296-1311 | `ci.md` | - | `51765d2ca790` | Define the done condition before you edit, and report four things when you deliver. |
| 60 | 1312-1346 | `browser.md` | - | `52059a35867c` | The browser add-on is proven in the browser the user actually has, against an ISOLATED daemon — never by loading a bundle that dials 8787. (v1.259.0) |
| 61 | 1347-1362 | `browser.md` | - | `757d8295669f` | The browser add-on wears the app's icon, and a missing icon is a refused folder (v1.400.6) |
| 62 | 1363-1446 | `browser.md` | - | `1b76b9f6d935` | One card for a whole form; Point at it (v1.400.9) |
| 63 | 1447-1501 | `browser.md` | - | `41ee7d4f69c8` | A task the user RECORDS in the browser becomes a skill; the recorder never reads a value; a replay re-finds every element by role and name (v1.458.0) |
| 64 | 1502-1556 | `browser.md` | - | `2baa1d4a8b91` | The browser sidebar has skills, the user's browsing preferences, and a pointer the user can watch (v1.454.0) |
| 65 | 1557-1578 | `desktop.md` | - | `919944c16296` | Updates come from the `updates` branch manifest first; GitHub's releases feed is the FALLBACK, and no raw HttpError ever reaches the Updates (v1.260.0) |
| 66 | 1579-1600 | `browser.md` | - | `73d41b9e3b46` | The sidebar is armed BY SURFACE, and a browser-agent turn stays in chat (v1.262.0) |
| 67 | 1601-1650 | `providers.md` | - | `e8e1d22ebb0a` | A plan row is the model it runs; effort lists levels, Extra where the CLI says; the session's pane tabs are icons (v1.401.2) |
| 68 | 1651-1671 | `providers.md` | - | `86c60b9fa867` | The reasoning level is ONE vocabulary, ONE table, and applied only where the serving model offers it (v1.263.0) |
| 69 | 1672-1736 | `messaging.md` | - | `b1dea55c433e` | A remote agent is a CONVERSATION, both ways (v1.285.0) |
| 70 | 1737-1775 | `agents.md` | - | `931532196d52` | An @-mentioned agent is shown the CHAT, stays addressed, and hands WORK to a session (v1.284.0) |
| 71 | 1776-1805 | `desktop.md` | - | `416d37543652` | A module pops out into its own window, on the other screen when there is one (v1.283.0) |
| 72 | 1806-1819 | `chat.md` | - | `0fc0119b9bee` | A kept preference is said on the receipt, in the user's words (v1.282.0) |
| 73 | 1820-1828 | `build.md` | - | `5fea8ca13e9a` | A Build pane outside any project can make one in one press (v1.281.0) |
| 74 | 1829-1847 | `build.md` | - | `36cb71bd621c` | The Build assist carries the pane, the profile, the project and the lessons; dead pane keys are pruned; the job card remembers its target (v1.280.0) |
| 75 | 1848-1872 | `memory.md` | - | `fb5da79d766f` | Task reflections are not knowledge about the user; chat can remember a stated preference; the Memory page says what it knows (v1.279.0) |
| 76 | 1873-1897 | `chat.md` | - | `3ad9bd3483b0` | A named chat turn takes steer notes from any connection, and a sent message is editable (v1.278.0) |
| 77 | 1898-1918 | `browser.md` | - | `0771a1943df7` | A key opens the panel, Esc stops, Open Jarvis focuses, and the model menu is typed into; a gate proves concurrency by construction (v1.277.0) |
| 78 | 1919-1936 | `browser.md` | - | `d5fb52a6bd49` | The risk door's card is answered where the turn is; a named click reads first; a loading page is read twice (v1.276.0) |
| 79 | 1937-1952 | `chat.md` | - | `7a0fe7c2d75a` | A send never goes without its files, and a file alone is a message (v1.275.0) |
| 80 | 1953-1977 | `browser.md` | - | `c033d4768d7d` | The same failing call is not run a third time; the ledger is the backlog (v1.274.0) |
| 81 | 1978-1993 | `browser.md` | - | `285efbf5964d` | The tab the agent is working in GLOWS; the worker knows from the commands it answers (v1.273.0) |
| 82 | 1994-2010 | `browser.md` | `agents.md` | `7ad959610f89` | A side panel cannot show the microphone prompt; a page in a tab can (v1.272.0) |
| 83 | 2011-2023 | `browser.md` | - | `3139a1fd4e39` | A navigation is judged by its DESTINATION, never by the page it leaves (v1.271.1) |
| 84 | 2024-2040 | `browser.md` | - | `2ce2102ab3e3` | The sidebar works in the tab the user is looking at; a new tab is a thing the user asks for (v1.271.0) |
| 85 | 2041-2057 | `tools.md` | - | `29585d6405e9` | A covered call must CARRY its grant into the registry; a test that stubs `invoke` cannot see that it did not (v1.270.1) |
| 86 | 2058-2080 | `browser.md` | - | `f69db15b0be6` | The sidebar's conversation lives in the daemon; its ONE switch is the per-tab grant widened to every tab; its steps fold (v1.270.0) |
| 87 | 2081-2100 | `browser.md` | - | `f30ef50c2674` | The sidebar's composer owns the model icon and a mic/send button; dictation rides the panel socket into the app's OWN voice engine (v1.269.0) |
| 88 | 2101-2118 | `browser.md` | - | `e4f37281d54a` | The add-on's worker is kept alive by the DAEMON's heartbeat; a timer in the worker cannot do it (v1.268.0) |
| 89 | 2119-2139 | `browser.md` | - | `851dfbeac9be` | The sidebar prints only what changes what the user can do, and it has a model picker — the user's decision, reversing D28's "no model picker (v1.267.0) |
| 90 | 2140-2150 | `ci.md` | - | `03e30356613d` | `asyncio.sleep(d)` measures LESS than `d` on Windows; bill by an injected clock, never by a sleep (v1.266.1) |
| 91 | 2151-2171 | `browser.md` | - | `8e979f218eb9` | One approval per tab: the grant lives on the runtime, the lane consults it, the risk gate never does (v1.266.0) |
| 92 | 2172-2191 | `browser.md` | - | `4e8034a6541e` | Mint AFTER the check, and undo a mint that was not delivered (v1.265.0) |
| 93 | 2192-2213 | `desktop.md` | - | `245eadb646d9` | A browser tab has no token, and "restart" is not the answer to a 401 (v1.264.0) |
| 94 | 2214-2234 | `ci.md` | - | `a60b89fe2aa1` | The gate collects what CI collects, runs on CI's Python, and a hang fails BY NAME (v1.286.0) |
| 95 | 2235-2262 | `chat.md` | - | `90fcf5821031` | Stop is a KILL, a steer is never dropped, a tool round is budgeted (v1.287.0) |
| 96 | 2263-2304 | `daemon.md` | `automations.md` | `2d5ef9c212aa` | Background work you can trust (v1.292.0) |
| 97 | 2305-2352 | `connections.md` | - | `e265a3b6ef90` | Packs, spreadsheets and the phone (v1.291.0) |
| 98 | 2353-2411 | `tools.md` | - | `78bbe781610c` | Safety checks, other agents' history, and a read-only Memory door (v1.290.0) |
| 99 | 2412-2429 | `dashboard.md` | - | `27fea3896bdd` | An Overview tile never leaves the screen; pushing it off opens the module in its own window (v1.289.0) |
| 100 | 2430-2471 | `dashboard.md` | - | `07734f48c584` | The Overview desktop is three screens of ten — Office, Operations, System — and only the current screen is in the DOM (v1.293.0) |
| 101 | 2472-2518 | `agents.md` | - | `1e2c160ffdc2` | A custom agent is an EMPLOYEE: a job card, a monthly allowance, a day off (v1.295.0) |
| 102 | 2519-2577 | `agents.md` | - | `462a14e8908b` | An ASSIGNMENT is a durable job queued for a named agent; the dispatcher runs it when the agent is free (v1.296.0) |
| 103 | 2578-2637 | `agents.md` | `memory.md` | `e6fe7b5969fb` | An agent keeps a FOLDER, learns from a COACH, and its skills are CURATED (v1.297.0) |
| 104 | 2638-2705 | `tools.md` | `chat.md` | `45e681484126` | A run has a TRUST posture, and every injected context is SCANNED (v1.298.0) |
| 105 | 2706-2768 | `tools.md` | `automations.md`, `connections.md` | `8ef99a868943` | A standing grant is keyed on the EXACT arguments; a pack's new write tool is QUARANTINED; a schedule carries its own skills, folder, chain,  (v1.299.0) |
| 106 | 2769-2864 | `providers.md` | `agents.md` | `8248c1b62150` | claude-cli speaks the CLI's own protocol, behind a one-request relay (v1.300.0) |
| 107 | 2865-2940 | `providers.md` | - | `5fc609957e6f` | Iron-Proxy is the ACCOUNT MANAGER; Iron Jarvis still EXECUTES (v1.301.0) |
| 108 | 2941-2985 | `providers.md` | - | `e60cd42478d8` | A Build pane's ACCOUNT is chosen when the pane STARTS, and shown on it (v1.302.0) |
| 109 | 2986-3026 | `build.md` | - | `5803e05893a9` | Continue on the next account is DETECTED from Claude Code's own limit record, CHOSEN by session id, and COPIED, never moved (v1.303.0) |
| 110 | 3027-3047 | `build.md` | - | `2e31e0f5d8ec` | A Build pane and a `claude` child are TOP-LEVEL sessions: a parent Claude Code session's markers never cross (v1.303.1) |
| 111 | 3048-3084 | `build.md` | - | `8f4a8e4d64cc` | A carried conversation the next account refuses ends in a HANDOFF, never a dead end (v1.303.2) |
| 112 | 3085-3136 | `providers.md` | - | `3e43a1e41b88` | Iron-Proxy is TOLD to re-check a sign-in; it never notices by itself (v1.303.3) |
| 113 | 3137-3181 | `agents.md` | - | `01193a0083a0` | The Agents page is a set of PROJECT WORLDS; a project's room is grounded in that project and private to its table (v1.304.0) |
| 114 | 3182-3246 | `memory.md` | `chat.md` | `8ac7a9a1b50e` | A preference has a STATUS, and only a confirmed one reaches a prompt; the second same correction becomes ONE question (v1.305.0) |
| 115 | 3247-3324 | `memory.md` | `providers.md` | `177e40c759b3` | The profile reaches a Build pane ONLY through a switched-on, generated block in the CLI's own instructions file — outside text byte-identica (v1.306.0) |
| 116 | 3325-3371 | `chat.md` | - | `5592e2687bd7` | The composer is the only card; a question takes its place; a project folder's AGENTS.md / CLAUDE.md ride every chat turn (v1.326.0) |
| 117 | 3372-3439 | `providers.md` | - | `07e91994890a` | A LOCAL-ONLY chat keeps its content on this network; the catalog is cached and asked all at once; a box that just answered is not probed (v1.332.0) |
| 118 | 3440-3536 | `daemon.md` | `desktop.md` | `d9505c4d7d51` | Iron Jarvis must not eat the PC (v1.333.0) |
| 119 | 3537-3570 | `dashboard.md` | - | `9ee264cb6e6a` | The idle window's cost is its MOTION, and the dead ends are written down here so nobody walks them again (v1.415.0) |
| 120 | 3571-3618 | `dashboard.md` | `daemon.md` | `0d4c847841e6` | Everything › Status is the system map (v1.334.0) |
| 121 | 3619-3660 | `chat.md` | `daemon.md` | `9d592f023ea8` | A tool call is recorded BEFORE it runs; one daemon per home; a chat totals its own use; a too-long refusal is shrunk and tried once (v1.335.0) |
| 122 | 3661-3711 | `chat.md` | - | `d9483b27bf17` | A NAMED chat turn is the DAEMON's, not the connection's (v1.336.0) |
| 123 | 3712-3760 | `agents.md` | `daemon.md`, `build.md` | `092ad76590c8` | An agent run KEEPS its conversation; Continue after a restart RESUMES it (v1.337.0) |
| 124 | 3761-3796 | `daemon.md` | - | `39290ca0f91e` | The UPDATE REHEARSAL runs a real daemon through a restart, every release (v1.338.0) |
| 125 | 3797-3830 | `agents.md` | `browser.md` | `02dd9c115bc9` | The pi-durable borrow, finished: every surface rejoins, and a planned run resumes its steps (v1.339.0) |
| 126 | 3831-3847 | `tools.md` | - | `1c58e7180d45` | A fetched page brings its LINKS; a model must never have to guess a URL (v1.340.0) |
| 127 | 3848-3884 | `dashboard.md` | - | `36a8ce19b485` | A state that matters is never told by hue alone (v1.341.0) |
| 128 | 3885-3942 | `connections.md` | - | `af1c424bf9b8` | A pack SAYS WHY it failed, where the model and the user each read (v1.342.0) |
| 129 | 3943-3999 | `connections.md` | - | `598ec631bdf3` | A pack is added through ONE door, from any dialect, and a secret never crosses the browser (v1.343.0) |
| 130 | 4000-4111 | `cross-area.md` | `messaging.md` | `f84325fc3f6e` | A failed run reads in plain words at EVERY door; one Sign in press; every built-in worded; a waiting chat is seen where the user is; Continu (v1.388.0) |
| 131 | 4112-4129 | `daemon.md` | - | `234f105d59d9` | NEVER `while tasks: await asyncio.gather(*tasks)` — it SPINS (v1.388.2) |
| 132 | 4130-4183 | `memory.md` | - | `87f4f79310cf` | An MCP memory base WRITES only through tools the user CONFIRMED; a refusal is never "unavailable"; a save that landed is never sent twice (v1.408.0) |
| 133 | 4184-4258 | `memory.md` | `chat.md`, `tools.md` | `8b48a1573123` | A BRAIN PROFILE names the durable memory base; ONE resolve rule; the guide is trusted, every retrieved note is DATA; a save is "saved" only  (v1.406.0) |
| 134 | 4259-4334 | `build.md` | `context.md` | `58c666804bec` | The Session tab's CONTEXT drop-down: what fills the conversation, part by part, in BOTH coding-session views; a part nobody reported is neve (v1.415.0) |
| 135 | 4335-4345 | `providers.md` | - | `b5766f930824` | A user's words never run one of Claude Code's OWN slash commands (v1.416.0) |
| 136 | 4346-4373 | `build.md` | - | `daf7d222a81a` | A step is "running" only while its coding tool runs it (v1.427.2) |
| 137 | 4374-4467 | `providers.md` | `build.md` | `56cff34df27a` | Two Iron-Proxy Claude accounts can be ONE Claude login; Continue never moves between them, and a parked account can be tried anyway (v1.427.3) |
| 138 | 4468-4571 | `connections.md` | - | `1c1fd3f1832d` | Microsoft 365 is LOCKED DOWN for client data: read and draft only, one account, the firm's own app, a locked install, the user's own models  (v1.428.0) |
| 139 | 4572-4613 | `build.md` | - | `af52027fd7ab` | A Code-view session whose tool still runs BACKGROUND work is never reaped or evicted (v1.418.0) |
| 140 | 4614-4733 | `build.md` | `providers.md` | `aa3f4f80b8ad` | Build's CODE view drives the user's REAL Claude Code / Codex with their OWN tools ON; Iron Jarvis only starts them, draws them and answers t (v1.409.0) |
| 141 | 4734-4811 | `build.md` | - | `4f3e6c709823` | Pi in Build › Code offers what Pi's terminal offers: Pi's OWN model list (read in the background before any session), each model's OWN think (v1.429.0) |
| 142 | 4812-4875 | `build.md` | `agents.md` | `d5df696293fd` | A Pi folder's OWN extensions are loaded only by the user's press, for that session (`pi --approve`); Iron Jarvis says when Pi skips them (v1.431.0) |
| 143 | 4876-4957 | `build.md` | - | `bc58ad966a91` | Build › Code: "Allow all commands this session" is Iron Jarvis's OWN whole-tool session rule; the plan is on its card; a failure names the t (v1.417.0) |
| 144 | 4958-5059 | `build.md` | `desktop.md` | `ef2abbadff36` | A coding session STARTS AT THE COMPOSER: no form, no folder until one is chosen; Choose… is Windows' own folder picker; a tool with several  (v1.412.0) |
| 145 | 5060-5163 | `build.md` | `agents.md` | `014037378381` | SAME RELEASE (v1.412.0): Build › Code takes PICTURES, changes MODE at any time, and a note typed while it works STEERS the running reply (v1.412.0) |
| 146 | 5164-5264 | `build.md` | `agents.md` | `eb3cb6ece292` | OpenCode is the FOURTH tool in Build › Code: `opencode acp` (the Agent Client Protocol, JSON-RPC over stdio), its own tools/agents/plugins/M (v1.413.0) |
| 147 | 5265-5367 | `build.md` | - | `5334c62826de` | Grok is the FIFTH tool in Build › Code: `grok agent --no-leader stdio` (ACP, the core OpenCode speaks, LIFTED into `hosted/acp.py`); a mode  (v1.416.0) |
| 148 | 5368-5589 | `build.md` | - | `b299a981dd60` | Build › Code is part of the app: its running work is seen everywhere, its card reaches the phone, and the REAL tool may get Iron Jarvis's ow (v1.414.0) |
| 149 | 5590-5599 | `chat.md` | - | `fc8e2532c85c` | A stream response CLOSES its stream when it stops (v1.414.6) |
| 150 | 5600-5606 | `build.md` | - | `47d120dde212` | A Build › Code reply ends only after the tool's own session id is on the row (v1.414.4) |
| 151 | 5607-5625 | `daemon.md` | - | `73b5ecc35b99` | Python's `platform` module never asks WMI in this app (v1.414.3) |
| 152 | 5626-5637 | `build.md` | `providers.md` | `27295020d2c2` | A Build › Code tool signed out on this PC is still offered when an Iron-Proxy account can run it (v1.414.2) |
| 153 | 5638-5652 | `build.md` | - | `19cecc4b7318` | A Build › Code reply ends on the answer to ITS message, never on earlier work (v1.414.0) |
| 154 | 5653-5737 | `memory.md` | `build.md` | `ef28afa21135` | A project KNOWS its coding sessions: resolved by FOLDER at read time, merged into "Recent activity", and a repeated correction in Chat › Cod (v1.414.0) |
| 155 | 5738-5862 | `build.md` | - | `dadf2adb476f` | A coding session's Changes tab is ONE scrolling review, and every file, hunk, step and finding opens in VS Code / Cursor at its line — the d (v1.405.0) |
| 156 | 5863-5947 | `desktop.md` | `ci.md` | `7565aa415cad` | The STABLE update channel: a release reaches `updates-stable` only once it has been out a day, no newer patch replaced it, its commit's Test (v1.405.0) |
| 157 | 5948-5983 | `build.md` | - | `ebe94acbf238` | A grant in a coding session is the SESSION's, not one helper's (v1.404.1) |
| 158 | 5984-6119 | `build.md` | - | `cfd0657e5b12` | A coding session's PULL REQUEST is watched; a failed check is fixed BY THE SESSION, reviewed by a CLOUD reviewer, and only an approved fix i (v1.407.0) |
| 159 | 6120-6230 | `tools.md` | `chat.md` | `56d4d6197ea1` | ONE permission switch: Accept edits and Auto START from Ask first's cards and only remove some; risky always asks; a model may only say allo (v1.407.0) |
| 160 | 6231-6296 | `build.md` | - | `b21850d881aa` | The NEXT-STEP BOX under the newest settled reply is DERIVED, never saved; every button is an existing action; the user's own decisions are o (v1.407.0) |
| 161 | 6297-6353 | `tools.md` | - | `7842b4dd1900` | Ask first asks before an edit of Git's and Jarvis's own files; a closing offer is the user's when ANY clause of it is (v1.408.1) |
| 162 | 6354-6438 | `build.md` | `messaging.md` | `5545cc6305a0` | The coding sessions are the CHAT page's Code view; Build is the terminals; the sidebar is five rooms (v1.407.0) |
| 163 | 6439-6501 | `build.md` | - | `93e497d46860` | Two coding sessions SIDE BY SIDE: one shared pane column, keys stay in the focused column, both are looked at (v1.407.0) |
| 164 | 6502-6553 | `build.md` | - | `ff19697c7728` | A coding session's SESSION tab reads what the app already knows; its one new read is the session's background programs (v1.404.0) |
| 165 | 6554-6610 | `build.md` | - | `f9db9b47ae68` | A CODING SESSION CAN HAVE A TEAM: the lead plans and checks and NEVER edits; workers do the code; the reviewer checks every change; a lead t (v1.403.0) |
| 166 | 6611-6683 | `cross-area.md` | `build.md` | `f39cc65976e2` | APPROVE ONCE, THEN OFF TO THE RACES: every ask is counted, an approved plan runs, runs you start do not stop at 12, and a closing offer is a (v1.402.0) |
| 167 | 6684-6719 | `build.md` | `agents.md` | `ea20c94b032b` | TEAM REVIEW AT THE FINISH: Iron Jarvis hands the change to the team's reviewer ITSELF; the reviewer only reads, is bounded, and its verdict  (v1.401.0) |
| 168 | 6720-6752 | `agents.md` | - | `32159feb7a49` | TEAM FIRST: a coding session's planner can be TOLD to hand pieces to the team; off is byte-identical (v1.400.0) |
| 169 | 6753-6818 | `build.md` | `ci.md` | `44645494e0fb` | The bench's TEAM tier: six tasks that split into pieces; `--tier` takes a comma list (v1.400.0) |
| 170 | 6819-6842 | `chat.md` | - | `b7f0ac974fa9` | The chat toolbar stays ONE ROW; it gives up words one chip at a time, least needed first (v1.400.0) |
| 171 | 6843-6926 | `build.md` | `messaging.md` | `f833c76603a2` | NIGHT SHIFT N2: Iron Jarvis commits each ended night task to ITS OWN branch (screened), and ONE summary per night is announced ONCE (v1.399.0) |
| 172 | 6927-7001 | `build.md` | - | `510700dc263f` | NIGHT SHIFT N1: a queue of coding tasks that run overnight on their own branches; file edits and commands run unasked, EVERYTHING else is ca (v1.398.0) |
| 173 | 7002-7142 | `build.md` | `desktop.md` | `5bfbfa910816` | TRY ON SEVERAL MODELS: up to three attempts, each its own branch, worktree, model and account; opt-in by the user's words or the Try on… chi (v1.397.0) |
| 174 | 7143-7221 | `build.md` | `providers.md` | `983282eda80d` | The bench MEASURES a team: one command runs the team, then the planner alone, and refuses the night before it starts (v1.396.0) |
| 175 | 7222-7289 | `agents.md` | `providers.md` | `4e9101d2a680` | The planner asks for a ROLE; the team router picks the member, local first, never past an account's limit, never across an account group (v1.395.0) |
| 176 | 7290-7364 | `agents.md` | `build.md` | `ed473598b3b3` | A MODEL TEAM member is a custom agent with ROLES; it runs on ITS OWN model, held, on an account the user's groups allow (v1.394.0) |
| 177 | 7365-7406 | `build.md` | `messaging.md` | `b3591cbe6cb0` | An account limit is WAITED OUT by itself; a move to another account happens only WITHIN a group the user made, same vendor (v1.393.0) |
| 178 | 7407-7459 | `build.md` | `messaging.md` | `285305b0fa90` | A coding reply NEVER STALLS SILENTLY: a stall notice with Stop the step, a question to the phone, a summary mid-reply, an optional cap (v1.392.0) |
| 179 | 7460-7506 | `build.md` | - | `49e2e8879741` | A coding reply SURVIVES sleep and restarts: awake while WORKING, its own record kept per round, carried on by itself exactly once (v1.391.0) |
| 180 | 7507-7580 | `build.md` | `ci.md` | `a3d7a36f6121` | A coding reply's TIMELINE: one precise clock for LENGTHS, the wall clock for WINDOWS (v1.390.0) |
| 181 | 7581-7631 | `build.md` | - | `7928605ead14` | A coding-session reply is a reply the whole app can see; a failed one has a press; the view's name lives in ONE constant (v1.389.0) |
| 182 | 7632-7697 | `providers.md` | `context.md` | `9bd554830053` | A LOCAL endpoint's reply budget fits the work; a reply CUT at the limit never runs its half call and is picked up again (v1.387.2) |
| 183 | 7698-7735 | `build.md` | - | `55b299fb2aca` | The finish check sees files a reply changed THROUGH COMMANDS: a folder snapshot beside the journal (v1.387.1) |
| 184 | 7736-7787 | `build.md` | - | `6460bfd0c65d` | A Build turn starts with a PROJECT MAP; the project's OWN checkers run after an edit as GATED calls that need a person; a made-up tool name  (v1.387.0) |
| 185 | 7788-7837 | `build.md` | - | `dddcfc4d6d37` | The claude dialect's `Bash` RUNS IN BASH on Windows; wherever cmd.exe still runs a model's command, a `;` guard refuses the destructive case (v1.386.2) |
| 186 | 7838-7907 | `build.md` | - | `ab53dad457c9` | A Build reply that changed files CHECKS ITS OWN WORK once before it ends, through the same gates as any call (v1.386.0) |
| 187 | 7908-7990 | `build.md` | `chat.md` | `653e07a7da56` | A Build reply works until it is DONE: no fixed step limit, never stopped for being unwatched, resumed from where the page was, the plan on t (v1.385.0) |
| 188 | 7991-8083 | `build.md` | `ci.md` | `91378685cf8c` | The benchmark can RANK: repeats with a range, the exact model, a `frontier` tier, and every run worked ALONE (v1.384.0) |
| 189 | 8084-8134 | `chat.md` | - | `34b5b1d7753c` | The composer's model menu is PORTALED, FIXED at its chip and always inside the window (v1.382.0) |
| 190 | 8135-8217 | `providers.md` | - | `ca3b8552f6e0` | Every request to a saved endpoint carries ITS saved key, and only to that server; nothing reaches a LiteLLM gateway the Ollama way (v1.382.0) |
| 191 | 8218-8240 | `providers.md` | - | `e3640078016d` | An account Iron-Proxy says is READY is never "needs to sign in again" because of an old note (v1.382.0) |
| 192 | 8241-8296 | `build.md` | - | `55b9cac35f56` | Shell lines from a PROJECT's or PLUGIN's command file always ask — in every posture, past every grant; the user's own command files follow t (v1.381.0) |
| 193 | 8297-8311 | `desktop.md` | - | `6f85d74d831e` | The app icon is the user's "iU" tile, at every size Windows draws (v1.380.0) |
| 194 | 8312-8419 | `memory.md` | `chat.md` | `70b21ab2174e` | A skill works in a coding session the way the harness it came from runs it: the same discovery, listed up front, Claude Code's base-director (v1.418.0) |
| 195 | 8420-8531 | `build.md` | `tools.md` | `27da0fa0dd2d` | Claude Code's `/` in the IJ harness: a typed `/name words` RUNS it; Claude Code's own command files are read, never executed; a Build sessio (v1.379.0) |
| 196 | 8532-8580 | `build.md` | - | `efd6caedc56e` | The IJ harness is MEASURED again: a benchmark turn runs AS a Build session; the walkthrough's gaps are written down (v1.378.0) |
| 197 | 8581-8649 | `providers.md` | - | `6a5340f76088` | A provider's safety REFUSAL is a typed error with a way out — never an empty reply, never retried, never moved to another provider (v1.378.0) |
| 198 | 8650-8730 | `build.md` | `providers.md` | `f4b9877aa8c1` | A Build session's commands see the user's PATH and home, never a secret; the rest of the app stays scrubbed (v1.377.0) |
| 199 | 8731-8852 | `build.md` | `ci.md`, `providers.md` | `db1e09486dd9` | A Build session's agent drives its OWN invisible Edge on local pages only; page text is untrusted; nothing outlives the session; and the har (v1.376.0) |
| 200 | 8853-8940 | `build.md` | - | `8d0eb2e185f0` | A Build session in a git folder works on ITS OWN BRANCH in a worktree BESIDE the repository; the user's main checkout is touched by Merge on (v1.375.0) |
| 201 | 8941-9030 | `build.md` | `context.md` | `2834c31ab354` | A Build session SHOWS its work: the plan under the reply, how full the conversation is, a session summary only Compact writes, and one notic (v1.374.0) |
| 202 | 9031-9100 | `build.md` | - | `30646919cc93` | `/` and `@` in a Build session's box; the app's font is SHIPPED; the harness reads like a document (v1.373.0) |
| 203 | 9101-9195 | `build.md` | `agents.md`, `memory.md` | `8d91e560be08` | A Build session can start HELPERS, and a Build turn is HELD on its model, pinned account or not (v1.372.0) |
| 204 | 9196-9291 | `build.md` | `providers.md` | `21ff510339fb` | The IJ harness SEES: a picture rides the message and the tool result; the Browser pane's page is looked at without asking, any other local p (v1.371.0) |
| 205 | 9292-9431 | `build.md` | - | `77f3e3f4e637` | Rewind is ALL OR NOTHING through the undo journal; the library PROPOSES and the user accepts; the refine loop ADOPTS only into its own folde (v1.370.0) |
| 206 | 9432-9476 | `build.md` | - | `f302810e4b2c` | The IJ harness is a VIEW of Build built from the chat's own pieces; the daemon writes the transcript, and a turn's end is on the record BEFO (v1.369.0) |
| 207 | 9477-9521 | `providers.md` | - | `545950c64819` | A Build session is PINNED to one account; a lost pin is a rotation, so the pin rides a ContextVar that every lease path copies (v1.368.0) |
| 208 | 9522-9576 | `build.md` | `chat.md` | `008a05dd74ff` | A Build session is a chat ROW that is never a chat; its turn is the daemon's; plan mode asks ONCE and nothing grants it (v1.367.0) |
| 209 | 9577-9618 | `build.md` | `chat.md` | `1cbaace62460` | Harness mode: one switch, a dialect per model family, a component tree per folder (v1.366.0) |
| 210 | 9619-9632 | `providers.md` | - | `17aeafe9aee4` | A renamed endpoint slot is still the slot (v1.365.3) |
| 211 | 9633-9672 | `providers.md` | - | `36ba902f3309` | codex-cli speaks Codex's own app-server protocol, and Jarvis runs every tool (v1.365.0) |
| 212 | 9673-9710 | `build.md` | - | `335ab09949bf` | The coding tools Claude Code and Codex give their models, on Jarvis's own registry (v1.364.0) |
| 213 | 9711-9773 | `build.md` | `chat.md` | `2a869ab22cee` | The IJ harness is MEASURED: `ironjarvis bench-harness` (v1.363.0) |
| 214 | 9774-9815 | `providers.md` | - | `26b8848011da` | "Stays on this network" is judged per MODEL: a name a proxy sends to the cloud is the cloud (v1.362.0) |
| 215 | 9816-9904 | `cross-area.md` | `desktop.md`, `automations.md` | `af6fad7d1d6f` | No notice lost: ONE window speaks, a workflow's notice stands in only for the step that ENDED it, a schedule's line names its failed step; t (v1.361.0) |
| 216 | 9905-9989 | `cross-area.md` | `messaging.md` | `f2e8190d79f5` | ONE notice per outcome and per ask on this PC; the Connect door only for the user's own data; the user's model server by its name everywhere (v1.360.0) |
| 217 | 9990-10080 | `cross-area.md` | `chat.md`, `agents.md`, `connections.md` | `2ebb8970e2b7` | A run speaks in the log's words, the Connect door never waits for the model, a model is named the menu's way in every list, and Emergency st (v1.359.0) |
| 218 | 10081-10214 | `cross-area.md` | `chat.md` | `55f39168d05b` | Every surface gives ONE verdict per run; chat picks the right tools; the Directory and chat agree; another computer comes in by a setting (v1.358.0) |
| 219 | 10215-10323 | `cross-area.md` | `messaging.md`, `connections.md` | `d1c4be571edc` | Another Jarvis is a teammate through ONE door; every pack lives in ONE catalog file; a saved web service is callable, carded and kept away;  (v1.357.0) |
| 220 | 10324-10406 | `cross-area.md` | `connections.md`, `tools.md` | `8b2b7bd1c7d3` | A switched-off pack reads OFF on every surface; an app that signs in on this PC is ONE row from every door; the calm chip is the default (v1.356.0) |
| 221 | 10407-10499 | `cross-area.md` | `connections.md`, `chat.md` | `becd8bf5e247` | A failure carries its press on BOTH lanes; a pack has ONE add/remove door; an imported placeholder is ASKED, never stored (v1.355.0) |
| 222 | 10500-10580 | `cross-area.md` | `providers.md`, `messaging.md`, `connections.md` | `b88bd0a52c10` | ONE sentence per cause, in the app's name, wherever the user meets it (v1.354.0) |
| 223 | 10581-10665 | `cross-area.md` | `chat.md` | `62677d76f120` | ONE place for each kind of thing, and a refusal leaves the door the other surfaces already use (v1.353.0) |
| 224 | 10666-10751 | `cross-area.md` | `agents.md`, `chat.md`, `automations.md` | `37dc27cea92b` | One vocabulary, one receipt, one approval language (v1.352.0) |
| 225 | 10752-10856 | `cross-area.md` | `agents.md`, `automations.md`, `messaging.md` | `3d0fde24f0d2` | Every surface tells the truth about an outcome — ONE verdict vocabulary, and Emergency stop stops what starts on its own (v1.351.0) |
| 226 | 10857-10881 | `connections.md` | - | `0f95b46f88b6` | A catalog pack that STARTS today: an unpinned uvx/npx package can break when an upstream library moves (v1.460.0) |
| 227 | 10882-10925 | `connections.md` | - | `53af8b1faa62` | A catalog names a package that EXISTS; a proxy's picker offers its real models (v1.350.1) |
| 228 | 10926-10987 | `agents.md` | - | `166032b57360` | A helper agent's plan is a checklist; approving it first is an OFF-by- default setting (v1.350.0) |
| 229 | 10988-11024 | `chat.md` | - | `6bf8b8d0227b` | A step can be opened; a helper agent is one row (v1.349.0) |
| 230 | 11025-11064 | `chat.md` | - | `162cc7f94c24` | The chat shows its work: ideas from agent-elements, never its boxes (v1.348.0) |
| 231 | 11065-11201 | `documents.md` | `chat.md`, `tools.md` | `53a5119b0b7b` | Media is QUOTED before it is made, a spend is CARDED, free is free, a long render comes home through the daemon (v1.346.0) |
| 232 | 11202-11258 | `chat.md` | - | `013e85cec789` | A line that arrives while nobody is looking is written by the DAEMON, into the chat it belongs to (v1.345.0) |
| 233 | 11259-11337 | `chat.md` | - | `803eae56eeed` | Jarvis reads its own health through TOOLS, repairs only with a card, and a failed call is answered by a rule (v1.344.0) |
| 234 | 11338-11386 | `chat.md` | `providers.md` | `777a667f34fa` | The calm chat, finished (v1.330.0) |
| 235 | 11387-11446 | `chat.md` | `providers.md` | `df51ef1f3ed8` | One calm app (v1.329.0) |
| 236 | 11447-11475 | `chat.md` | - | `d266f2e76404` | @ another chat, what a reply changed, archive (v1.328.0) |
| 237 | 11476-11518 | `chat.md` | `ci.md`, `agents.md` | `52237dd1a790` | The chat list is grouped and lit, one permission chip, the receipt names its rules and refs (v1.327.0) |
| 238 | 11519-11609 | `chat.md` | `ci.md` | `57783a50aabe` | A replaced answer is KEPT, a queued message waits for a clean finish, and page text is an attachment (v1.325.0) |
| 239 | 11610-11649 | `connections.md` | - | `1818de8005cc` | A pack can ASK only where someone can answer, and a pack's model request is answered by THE TURN'S model or refused (v1.324.0) |
| 240 | 11650-11698 | `chat.md` | - | `f1c7b3abb355` | Thinking is a FRAME, never answer text; `truncated` is honest; a Continue is ONE reply (v1.323.0) |
| 241 | 11699-11743 | `tools.md` | - | `42f28d6accf2` | A web image waits for a press; web_fetch is vetted by DNS and pinned; an MCP answer is the message with OUR id (v1.322.0) |
| 242 | 11744-11807 | `settings.md` | `dashboard.md` | `2e3bca8349c1` | The calm UI: ONE settings schema, ONE writer, chat changes with Undo, a four-item sidebar (v1.321.0) |
| 243 | 11808-11842 | `chat.md` | `ci.md` | `3b8db888da3d` | A chat reply can be rated; "Teach it your style" names what to do (v1.320.0) |
| 244 | 11843-11858 | `settings.md` | - | `7d955f0d72f7` | Settings in Simple is the everyday three (v1.319.0) |
| 245 | 11859-11888 | `dashboard.md` | - | `90623ffd4051` | Simple mode is a different SHAPE, not a filtered list (v1.318.0) |
| 246 | 11889-11925 | `dashboard.md` | - | `ae8d866bdb01` | Light is a SCHEME, and a theme can be the user's own (v1.317.0) |
| 247 | 11926-11958 | `dashboard.md` | - | `b0e67a839356` | Labels are tied, names read as names, a capped canvas, an estimate is never spend (v1.316.0) |
| 248 | 11959-11988 | `dashboard.md` | - | `edf5c820666f` | The busy surfaces, calmer: one AI name, a findable board, a story first (v1.315.0) |
| 249 | 11989-12021 | `dashboard.md` | - | `8f9d1068a96b` | A user reads WORDS, never ids; an empty page shows the way; the demo strip is subtracted from every full-height module (v1.314.0) |
| 250 | 12022-12066 | `dashboard.md` | - | `484677862d68` | The shell never claims a model during a demo; colour lives in TONE tokens; a phone page never pans sideways (v1.313.0) |
| 251 | 12067-12108 | `chat.md` | `ci.md` | `251875f969e7` | When something goes wrong, the user sees what happened and has a way forward (v1.312.0) |
| 252 | 12109-12159 | `cross-area.md` | `chat.md` | `464043e8d203` | Fast where people wait: the turn, the boot, the poll (v1.311.0) |
| 253 | 12160-12205 | `dashboard.md` | `desktop.md` | `7d7e1c72c3aa` | The first answer is REAL, the wizard tells the truth, and the first screen leads somewhere (v1.310.0) |
| 254 | 12206-12248 | `agents.md` | - | `76c1081aa635` | A mission's teammates can ASK, can be STOPPED, are VISIBLE when remote, and a mission is never a dead end (v1.309.0) |
| 255 | 12249-12283 | `agents.md` | - | `fa6d319092f4` | The round table is GONE from the dashboard; missions are the only way team work is given, and a project's TEAM is enforced (v1.308.0) |
| 256 | 12284-12327 | `agents.md` | - | `654967ae1d1d` | The Agents page is a MISSION screen: one objective, Jarvis runs the team, the deliverable first (v1.307.0) |
| 257 | 12328-12376 | `agents.md` | - | `d55b45973603` | An agent's run ends honestly (v1.288.0) |
| 258 | 12377-12407 | `documents.md` | - | `a624e3577675` | NO WinRT IN THE DAEMON PROCESS: Windows OCR runs in a killable child (v1.410.2) |
| 259 | 12408-12463 | `dashboard.md` | - | `08f2e435809f` | An animated background is DOWNLOADED, VERIFIED and SEALED; the app ships only the catalog (v1.410.0) |
| 260 | 12464-12579 | `connections.md` | - | `6308a7620cc8` | An `npx -y` / `uvx` pack's server starts DIRECTLY once its launcher has SAID which files it runs; anything uncertain starts exactly as befor (v1.415.0) |
| 261 | 12580-12616 | `tools.md` | - | `64e260c4e916` | An approval card says WHY and WHO, Enter is never silent, and a button never promises a grant the daemon will not write (v1.417.0) |
| 262 | 12617-12666 | `build.md` | `chat.md` | `b3b93974de6e` | A coding session is drawn like the chat page: never redrawn per word, a reply that never blinks, the finish check off the live reply, drafts (v1.417.0) |
| 263 | 12667-12742 | `build.md` | - | `fffb715efe7d` | Chat › Code starts at the composer, reads a reply IN ORDER, has ONE line under each reply, and lists its archived sessions (v1.418.0) |
| 264 | 12743-12833 | `build.md` | - | `5cace4e6a4a1` | One approval means one approval in Chat › Code: a session grant reaches the RUNNING reply; read-only commands, Iron Jarvis's own checks unde (v1.417.0) |
| 265 | 12834-12879 | `build.md` | - | `8c145abef4d6` | The project's checkers run ONCE per reply, at the finish check, inside one 15 s budget; tsc only on a small TypeScript project (v1.420.0) |
| 266 | 12880-12897 | `ci.md` | - | `5600c87ea2d6` | v1.420.1 (test-only; v1.420.0's Tests gate went red on four pins, nothing shipped). (v1.420.1) |
| 267 | 12898-12937 | `build.md` | - | `32b37eec12e7` | A scroll box is the POSITIONING box for what it holds (v1.420.2) |
| 268 | 12938-12983 | `chat.md` | - | `2f6846e2042a` | Grounding has a SOFT DEADLINE; the project map is CACHED by its top two levels; a kept reply APPENDS; a token is never re-parsed (v1.418.0) |
| 269 | 12984-13067 | `build.md` | - | `12ec8b39ac1d` | Build › Code shows the work AS IT HAPPENS: a row from the first written character, helpers nested, the tool's own words, the rest of a long  (v1.418.0) |
| 270 | 13068-13149 | `build.md` | `ci.md` | `4627da171ba1` | An ended task is worded by what it WAS; a foreground command's end says nothing (v1.477.2) |
| 271 | 13150-13232 | `build.md` | - | `5103362a69f7` | Build › Code's history STAYS (paged by a cursor, the tool's own transcript merged in), a view is served from MEMORY, and a message sent whil (v1.418.0) |
| 272 | 13233-13272 | `chat.md` | - | `71dee587d649` | A follow-up turn is TOLD its last reply's tool work; the prompt's STABLE head comes first; a coding turn drops chat-only blocks; Codex plans (v1.420.0) |
| 273 | 13273-13322 | `build.md` | - | `f7fb8ce5fdd6` | Build › Code's OpenCode never CHANGES a rule of the user's (looser or tighter); Codex on Windows has no "edits free, commands ask" (v1.420.0) |
| 274 | 13323-13369 | `providers.md` | - | `9e8d5014f30a` | A claude-cli round is RELEASED at `result`, torn down in the background, and rides ONE listener, ONE TLS context and a kept upstream connect (v1.420.0) |
| 275 | 13370-13450 | `providers.md` | - | `eae662c8d3e1` | Inside an open turn scope a claude-cli reply keeps ONE `claude`; the relay HOLDS Claude Code's follow-up and fills in the REAL tool results (v1.427.0) |
| 276 | 13451-13519 | `chat.md` | `build.md` | `b65674e5e076` | "My latest emails" is the mail KIND, a feature question asks the Guide, and a Build › Code file can be reverted per message (v1.420.0) |
| 277 | 13520-13566 | `dashboard.md` | `build.md` | `de075a1479e2` | Two views called Code are told apart by the TOOL; the approval levels are named by what they do; the engine-room words leave the screen (v1.420.0) |
| 278 | 13567-13623 | `build.md` | - | `e8458705e070` | A coding session's keys work from its PORTALED list; a long question's title is the first reply's words; the panes poll, open where the work (v1.420.0) |
| 279 | 13624-13694 | `agents.md` | - | `d8f74e1a0e2b` | An @-agent's failure is said in words and its round can be stopped; a connection Test answers TYPED facts the route words; a few missing doo (v1.420.0) |
| 280 | 13695-13776 | `messaging.md` | `providers.md`, `agents.md` | `65983e5341c0` | A remote's "accepted" has an end state; a helper's failure, an account refusal and a phone crash read in plain words; Grok has the Sign in p (v1.420.0) |
| 281 | 13777-13923 | `build.md` | `context.md`, `agents.md` | `60e910c6785a` | A team that works: a Build session's HELPERS run bash and see the session's standard, every member piece is recorded, the reviewer verifies  (v1.421.0) |
| 282 | 13924-14185 | `build.md` | - | `14667d3b9650` | Never lose work: a coding reply waits out a passing outage and keeps its rounds when it fails; Stop stops the running step; Build › Code's t (v1.422.0) |
| 283 | 14186-14282 | `agents.md` | `ci.md` | `960038226f80` | A reviewer's findings are RECORDS; only a LATER write confirms a fix; "Copy as a fix request" reads the records, never a model (v1.423.0) |
| 284 | 14283-14394 | `providers.md` | `build.md` | `a3bc2749d819` | Every subscription, everywhere: failover never spends money the user did not choose; capability is per MODEL; Codex's own model list; an acc (v1.424.0) |
| 285 | 14395-14553 | `build.md` | `agents.md` | `a78961fe9a0d` | A member's CONFIRMED findings become working habits the owner accepts; a habit is MEASURED, never removed by a measure; the fix request read (v1.425.0) |
| 286 | 14554-14675 | `build.md` | `providers.md` | `c3c814078aeb` | What a coding session's model is told is VISIBLE; the chat's coding door carries the request; one writer per conversation between a terminal (v1.426.0) |
| 287 | 14676-14698 | `messaging.md` | - | `2eb7af92e8d9` | A destination's Edit KEEPS what is saved; two-way says why it cannot hear the user (v1.427.6) |
| 288 | 14699-14745 | `tools.md` | `agents.md` | `0b22c3bd47bc` | The approvals menu shows NAMES; a level's meaning shows beside its row after a pause (v1.430.0) |
| 289 | 14746-14865 | `cross-area.md` | `browser.md` | `e3ba29e3fb21` | Wave 1 of the chat-first plan: 17 verified bugs fixed (v1.433.0) |
| 290 | 14866-14962 | `settings.md` | `automations.md`, `chat.md` | `f72d81ce802f` | Wave 2 of the chat-first plan: chat finds the right tool, and every chat says which screen it is on (v1.434.0) |
| 291 | 14963-15059 | `settings.md` | - | `b77dc41ee112` | Wave 3 of the chat-first plan: projects, agents, apps, destinations, goals from Chat, Local only and Undo from chat (v1.435.0) |
| 292 | 15060-15128 | `chat.md` | `messaging.md`, `providers.md` | `27a0831aade3` | Wave 5 of the chat-first plan: a door to the exact page, plain "not offered" lines, and a note under a change chat claimed but did not make (v1.436.0) |
| 293 | 15129-15170 | `build.md` | - | `0529cdad7d19` | Chat › Code's new-session box is the session composer's own row (v1.437.0) |
| 294 | 15171-15252 | `settings.md` | `build.md` | `ba2d88cb5363` | Wave 4 of the chat-first plan: the night shift, a folder's coding setup, model servers, accounts, memory bases, preferences, personas, answe (v1.438.0) |
| 295 | 15253-15368 | `build.md` | `providers.md` | `21b79e58b45e` | Antigravity is the SIXTH tool in Build › Code; an account's usage is READ from its own CLI on a press or while it is in use; the parity tabl (v1.439.0) |
| 296 | 15369-15428 | `cross-area.md` | `providers.md`, `agents.md` | `38192d24a3d2` | Wave 6 of the chat-first plan: four features reconnected, dead code gone, and every uncalled route has a written reason (v1.440.0) |
| 297 | 15429-15535 | `build.md` | - | `a16edf0a7996` | Build › Code catches up with each tool's own terminal (v1.441.0) |
| 298 | 15536-15641 | `settings.md` | `chat.md` | `100e7b257991` | Wave 7 of the chat-first plan: the 30 setup requests are a TEST, each driven through the real stream lane; every gap they showed is closed o (v1.442.0) |
| 299 | 15642-15697 | `build.md` | `desktop.md` | `31b9e63a7ad5` | Antigravity's model list and sign-in are READ by `agy models` in the background, only once its own sign-in finished; a chat picks a FOLDER w (v1.443.0) |
| 300 | 15698-15734 | `cross-area.md` | - | `ca16dcb71bdd` | The first ten minutes (v1.445.0) |
| 301 | 15735-15767 | `build.md` | - | `111bbbfb192a` | Build › Code, every tool again; a calm sweep (v1.447.0) |
| 302 | 15768-15797 | `cross-area.md` | `automations.md` | `e69bb42dd1d8` | Nothing silent (v1.446.0) |
| 303 | 15798-15876 | `settings.md` | `chat.md`, `connections.md` | `6cc62c81ced6` | Wave A of the readiness plan: chat configures everything (v1.448.0) |
| 304 | 15877-16006 | `cross-area.md` | `messaging.md`, `tools.md` | `90cdbbe4504f` | Wave B of the readiness plan: "did it work?" is answered everywhere (v1.449.0) |
| 305 | 16007-16143 | `cross-area.md` | `connections.md`, `providers.md` | `cf2f32d5cedf` | Wave C of the readiness plan: the first run and the connections (v1.450.0) |
| 306 | 16144-16269 | `cross-area.md` | `build.md` | `553297b11c42` | Wave D of the readiness plan: Settings, words, the automations pages, Help (v1.451.0) |
| 307 | 16270-16319 | `cross-area.md` | `automations.md` | `c184725ddf83` | The owner's four answers (2026-11-08): plain page names, coding straight in the folder, ticked tools run unasked, Gmail/Slack apps skipped (v1.452.0) |
| 308 | 16320-16363 | `build.md` | - | `aca5eca1493b` | Archiving a Build › Code session writes a short Markdown summary from its OWN record, never a model (v1.453.0) |
| 309 | 16364-16443 | `cross-area.md` | `build.md` | `760a20b8fcb0` | Waves E–H of the v1.452.0 re-audit plan (0j.5): one word for each thing, "did it work?" to the end, chat finds the right thing, one Connecti (v1.452.0) |
| 310 | 16444-16467 | `desktop.md` | - | `49a87da0538f` | Text size is the window's ZOOM, from one per-device row (v1.456.0) |
| 311 | 16468-16518 | `providers.md` | `messaging.md` | `55e251dc23b3` | A streamed reply from the user's OWN model server may go silent 10 minutes; a chat's phone alert waits for the turn's end, names the chat, a (v1.455.1) |
| 312 | 16519-16656 | `documents.md` | - | `092327024074` | Spreadsheets are WORKED OUT by Iron Jarvis, checked against Excel itself; results are kept IN the file; every stated figure has a calculator (v1.465.0) |
| 313 | 16657-16789 | `documents.md` | `chat.md`, `tools.md` | `71c9b738d58f` | Live Excel: the workbook OPEN in Excel is reached through ONE short-lived child; a change is all-or-nothing, listed, undoable, and never sav (v1.466.0) |
| 314 | 16790-16976 | `documents.md` | `chat.md` | `9db622d31e71` | Matching two lists of transactions in whole cents; Excel's own features in a SAVED workbook; what only Excel can do safely is refused (v1.469.0) |
| 315 | 16977-17038 | `documents.md` | `chat.md` | `9b57b8347be0` | Jarvis beside Excel: the chat knows what is in front in Excel, by the user's own switch; a narrow chat window stays beside the workbook (v1.468.0) |
| 316 | 17039-17088 | `documents.md` | - | `58815ed4a20e` | How a number is built, what-if, goal seek, and a cell the user can jump to (v1.467.0) |
| 317 | 17089-17190 | `tools.md` | `chat.md` | `0b81c00bb796` | A tool script runs ISOLATED and reaches a tool only through the registry's gate; chat makes a skill when asked for one (v1.470.0) |
| 318 | 17191-17270 | `tools.md` | - | `bfcc06623a5d` | Every tool call's three ledger rows are PLAIN INSERTS of the ORM's own rows; the two chat matchers are measured on labelled sets; a script n (v1.471.0) |
| 319 | 17271-17300 | `desktop.md` | - | `81303803998f` | The installer is SIGNED when six secrets are set; a signed build is checked by Windows before it is kept (v1.470.1) |
| 320 | 17301-17328 | `build.md` | - | `8e573c6c0438` | A streamed word in Build › Code costs only its own work (v1.472.0) |
| 321 | 17329-17354 | `build.md` | - | `2044154eccf0` | A long Build › Code session draws its newest rows only (v1.473.0) |
| 322 | 17355-17398 | `build.md` | - | `2d5ed73124a8` | A Build › Code session's coding tool can hand a piece of work to another coding tool (v1.474.0) |
| 323 | 17399-17420 | `chat.md` | - | `9ccf2f87ebd0` | A chat turn's safety notice names its chat; a `/chat?thread=` link works while Chat is open (v1.477.1) |
| 324 | 17421-17515 | `chat.md` | - | `e613281c31bd` | The model can LOOK UP what Iron Jarvis can do; the word tables are MEASURED on a held-out set that must never be tuned to (v1.478.0) |
| 325 | 17516-17647 | `chat.md` | `tools.md` | `6d74639e0b19` | What a lookup FOUND joins the reply; a change it found ALWAYS shows a once-only card; nothing joins a reply that read flagged content (v1.479.0) |
| 326 | 17648-17772 | `chat.md` | - | `e33e02a32ca0` | A reply about to end on WORDS is asked once to look first (its own switch, off by default); a tool that asks is described by its CARD; the a (v1.480.0) |
| 327 | 17773-17846 | `chat.md` | - | `f47cef678a4b` | A task's required files are checked ON DISK before the reply ends; its write rules are enforced at the tool gate; a printed traceback is a f (v1.481.0) |
| 328 | 17847-17933 | `chat.md` | `connections.md` | `14b17596b84d` | A calendar connects through `app_connect`; the Guide has a page per setting; a "where is it" question goes to the Guide (v1.491.1) |
| 329 | 17934-17955 | `chat.md` | - | `bbdd6a531969` | A setting's lookup line says WHERE it is on the Settings page (v1.490.4) |
| 330 | 17956-18000 | `chat.md` | - | `ceae0d6b74b3` | `list_files` names EMPTY folders; a published figure ASKED FOR arms the web; the Haiku rate is 95% ON AVERAGE, 93-97% a run (v1.490.3) |
| 331 | 18001-18090 | `chat.md` | - | `bd9fb8990d47` | A SECOND LOOK before a held card stays held; a looked-up memory search asks only when a memory base is really off this PC; a refused read na (v1.490.2) |
| 332 | 18091-18207 | `chat.md` | `tools.md` | `67b19af0a8b2` | TWO READINGS MUST AGREE BEFORE A CARD: the routing call says whether the user ASKED; when it says no, no tool that asks first joins (v1.490.1) |
| 333 | 18208-18266 | `chat.md` | - | `b66444f793fc` | The three Feature lookup settings are ON by default; the suite runs with them OFF (v1.490.0) |
| 334 | 18267-18423 | `chat.md` | `tools.md` | `7799f09ade34` | A missing detail is asked on a CARD; a setting is PICKED on a card; Iron Jarvis can look a message up itself BESIDE the reply (v1.489.0) |
| 335 | 18424-18442 | `chat.md` | `settings.md` | `8877c92211c1` | A change asked for is made, not asked about again (v1.488.0) |
| 336 | 18443-18467 | `chat.md` | - | `8e0cfe17bbcb` | Four wordings chat missed (v1.487.0) |
| 337 | 18468-18485 | `automations.md` | - | `711935181745` | Chat lists the schedules; a calendar link's removal is said honestly (v1.486.0) |
| 338 | 18486-18512 | `settings.md` | - | `5f92ed1d0ecf` | Chat sets an agent's job card; `config_list` names the models; a held lookup match is named (v1.485.0) |
| 339 | 18513-18530 | `chat.md` | - | `c31f02f83c9a` | The lookup finds a setting that holds a private value (v1.484.0) |
| 340 | 18531-18544 | `automations.md` | - | `04b4812c6eb9` | Chat can make a plain TASK schedule (v1.483.0) |
| 341 | 18545-18579 | `chat.md` | - | `31577633fad2` | The capability index carries EVERYDAY PHRASINGS (search words only); the lookup names a request three ways; a saved preference no longer kee (v1.482.0) |
| 342 | 18580-18653 | `memory.md` | `build.md` | `24e4903e455a` | THE WORK RECORD: what Iron Jarvis did, in which folder, built only from its own records; a chat is told of a piece of work only after its SO (v1.491.0) |
| 343 | 18654-18813 | `memory.md` | `providers.md`, `build.md` | `89b39f1cf5eb` | A RECAP of finished work is written by the conversation's own model and kept only where the conversation's record supports it; it is told on (v1.494.0) |
| 344 | 18814-18901 | `memory.md` | `agents.md` | `4cf58dfa437d` | THE IMPROVEMENTS INBOX: one read over every waiting suggestion; it decides NOTHING itself (v1.496.0) |
| 345 | 18904-18914 | `daemon.md` | - | `db4c4873d985` | `src/iron_jarvis/daemon/` — `app.py` is factory + glue only (platform build, lifespan boot-rehydration + backg |
| 346 | 18915-18923 | `agents.md` | `providers.md` | `f3f9a0d621eb` | `agents/` — orchestrator (sessions/reviews/continue), runtime (the perceive→act loop), dynamic agents. `provid (v1.217.0) |
| 347 | 18924-18940 | `context.md` | - | `bd12d355abcf` | `repl/` — the session NAMESPACE (v1.159.0). `worker.py` is the child: stdlib-only (it is spawned from a frozen (v1.159.0) |
| 348 | 18941-18953 | `context.md` | - | `23169f9666f0` | `context/` — `budget.plan_history`: the per-turn CHAT history planner (pure, offline, deterministic recap), co |
| 349 | 18954-18980 | `memory.md` | - | `ac33c416ef94` | `guide/` — THE IRON JARVIS GUIDE (v1.224.0; v1.223.0 shipped it as a chat persona, which the user rejected — i (v1.224.0) |
| 350 | 18981-18985 | `memory.md` | - | `a59f99ae4b01` | `profile/` — the user profile (ONE row): `models` (record), `store` (read-never-writes, partial save), `preset |
| 351 | 18986-18992 | `memory.md` | `automations.md` | `d26bc944f2b8` | `skills/` — recursive discovery incl. `~/.claude/skills`, `~/.claude/plugins`, `~/.codex/skills` (`framework.p |
| 352 | 18993-19013 | `documents.md` | - | `c81f7b05d22b` | `documents/` — `pdf_classify.py` is the per-PAGE scan router (v1.176.0, wraps the MIT `pdf-inspector` Rust ext (v1.176.0) |
| 353 | 19014-19086 | `build.md` | - | `bb0b0093a7b5` | Build › Code's tool, model and effort are ONE picker (v1.475.0) |
| 354 | 19087-19186 | `build.md` | - | `354beb115f68` | A Store program is never asked into the pane job, and starts one at a time; a frozen loop says where it is stuck (v1.407.1) |
| 355 | 19187-19214 | `build.md` | - | `82d072a05fa0` | Build (`app/terminals/page.tsx`) (v1.409.0) |
| 356 | 19215-19221 | `dashboard.md` | - | `07896656cc53` | `dashboard/app/<route>/page.tsx` per page; shared in `dashboard/components/` (`ui.tsx` primitives, `AppSidebar (v1.321.0) |
| 357 | 19222-19226 | `desktop.md` | - | `d02b251f1aa5` | `desktop/main.js` — supervisor (auto-restart children), tray, global hotkeys (Ctrl+Shift+J window, Ctrl+Shift+ |
| 358 | 19249-19275 | `ci.md` | - | `c31b5925c284` | First steps off Windows: the suite runs on Linux and Mac as a TRIAL, and tests about Windows-only things are LISTED, not scattered (v1.472.1) |
| 359 | 19276-19312 | `ci.md` | `build.md` | `e0d72005e6a7` | Linux and Mac, round two: four real faults, each invisible on Windows (v1.472.2) |
| 360 | 19313-19341 | `ci.md` | - | `d290fc4fd3df` | The Linux preview is its OWN workflow, after the release, and can only ADD one file (v1.472.3) |
| 361 | 19342-19727 | `tools.md` | `chat.md`, `documents.md` | `55a373a41428` | A script may call many tools at once, hand a value to a file tool, and plan a batch; what it read stays where it was read; a saved skill is  (v1.476.0) |
| 362 | 19728-19792 | `build.md` | - | `e9d93c2a5cbc` | Build › Code's "working" sign: ONE ring in a slot that is always there; the light hops in steps, never turns smoothly (v1.477.0) |
| 363 | 19793-19897 | `build.md` | `chat.md` | `52a21ff7b75d` | A coding reply is CHECKED AGAIN until its check passes or a limit ends it; the line under an unfinished reply is Iron Jarvis's own; a reply  (v1.493.0) |
| 364 | 19898-19952 | `chat.md` | `agents.md` | `a291155bb329` | A plain chat reply and an agent run's answer are CHECKED AGAIN while they can still be fixed (v1.495.0) |
