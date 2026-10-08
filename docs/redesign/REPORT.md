# Calm UI Redesign — Phase 2 REPORT (in progress)

## Test files edited (non-negotiable 7: path/route updates only)

| File | Edit | Why |
|---|---|---|
| `tests/test_browser_settings_v1235.py` | `test_browser_is_named_in_the_live_rearm_groups` reads `settings/writer.py` (`_REARM_GROUPS`) instead of `routes/settings.py` | S2 moved the live re-arm table into the one settings writer (path update for relocated code; the assertion is unchanged: `browser` is a re-armed group) |
| `tests/test_roster_coverage_v1178.py` | **Contract extension (not a path update — flagged for VR):** the five settings tools (S3/S4) and the eight record tools (S5) added to `_OFF_ROSTER_BY_DESIGN` with the reason | The test's own instruction for a new chat-only tool ("add it ... WITH the reason it is deliberately unreachable"); the settings tools are chat-only by design, like the browser tools beside them. No assertion was weakened. |
| `dashboard/__tests__/home-simple-v1318.test.tsx` | **Removed (APPROVED Q18)** | Pinned the retired Simple home; replaced by T1 coverage (`sidebar-v1321`, e2e T1) |
| `dashboard/__tests__/settings-simple-v1319.test.tsx` | **Removed (APPROVED Q18)** | Pinned the retired short Settings view and `AdvancedOnly`; Settings is one form (S10 regroups it) |
| `dashboard/__tests__/ux-wave1-shell-v1313.test.tsx` | **Removed one Simple-mode block (APPROVED Q18)**: "CONTROL: the drawer still opens on ij:toggle-nav and lists the nav", which asserted the seven Simple places and the Advanced switch | The drawer's opening and its four items are pinned by `sidebar-v1321.test.tsx` |
| `tests/test_chat_turn_service.py` | **Contract extension (flagged for VR):** `config_cards` added to the exact response-key set, with the note the file keeps for every new key | The test is the registry of the chat response shape and documents each additive key; the new key is pinned by `test_settings_chat_tools_v1321.py`. |

## Plan steps (running log)

| Step | Commit | What landed | Proof |
|---|---|---|---|
| S0 | 6590d7a | `ui.page_opened` visits (`POST /ui/visit`, `GET /ui/visits`) + route beacon | `test_ui_visits_v1321.py`, `route-visit-v1321.test.tsx` |
| S1 | a1c3739 | ONE settings schema (`settings/schema.py`), `GET /settings/schema` | `test_settings_schema_v1321.py` |
| S2 | cefea51 | ONE writer (`settings/writer.py`): validation all-or-nothing, side effects, ledger row + undo; every settings route converted; per-device store | `test_settings_writer_v1321.py` (10 tests, 7 mutations all red) |
| S3+S4 | 68e9cfb | Chat settings tools generated from the schema (`config_list/set/change/change_protected/secret`), per-key tiers enforced in the tool, protected changes carded even in Auto-approve and never "Always"; "Setting changed: old → new [Undo]" cards in both chat lanes; credential card posting straight to `POST /config/secret` (names-only ledger, encrypted undo backup); composer secret-paste guard (Q9); `secret_set` armed in chat is swapped for the card; the chat sends its device id | backend `test_settings_chat_tools_v1321.py` (12 tests; 9 mutations all red), dashboard `chat-config-cards-v1321.test.tsx` (12 tests; 10 mutations all red) |

| S5 | 1482198 | The user's RECORDS from chat with an Undo (`settings/records.py`): `schedule_update/delete`, `workflow_update/delete/schedule`, `channel_toggle`, `channel_connect`, `app_connect` (ask tier, chat-only, low trust denies them); `schedule_create`/`workflow_create` gain an Undo; a `record_restore` undo kind; "Schedule changed / Workflow removed … [Undo]" cards; a channel's or app's token arrives through the secure card (`app.<id>` resolves to the vault name the pack launches with, and saving it loads the pack) | backend `test_settings_records_v1321.py` (11 tests incl. T5 "schedule"; 15 mutations all red), dashboard record-card tests (3; 4 mutations all red) |
| S6 | 84d6af6 | ONE surface manifest (`lib/surfaces.ts`: every route, its Everything column, the sidebar's four items, pins ≤ 3); the `/everything` page (Work, Automations, Knowledge, System, Setup; filter; pin); the palette reads the manifest (the Directory and Everything were not reachable from it) and gains a row per setting plus a "do it in chat" row that only types the request | `everything-v1321.test.tsx` (8 tests; 7 mutations all red) |
| S7 | (this commit) | The persistent sidebar (`components/AppSidebar.tsx`): New chat, the four items (Build, Projects, Everything, Settings) from the manifest, pins (≤ 3) under them, Chats and Projects, and a footer with the status dot (→ Everything › Status), the mood orb and the Help menu (Q17). On the chat surface the CHATS space is the chat page's own thread rail, portaled in (Q5, layout only); on a phone the same body is the ☰ drawer. Retired (Q2): the Simple/Advanced switch (`lib/uiMode.ts`), the seven hubs (`lib/hubs.ts`), the tab row (`HubTabs`), `AdvancedOnly`, the Simple home (`HomeStart`) and the short Settings view; the theme left the title bar (Settings › Appearance, palette, phone drawer); the ☰ is phone-only | `sidebar-v1321.test.tsx` (12) + `chat-sidebar-rail-v1321.test.tsx` (4); 10 mutations all red |

## Commands run (latest)

| Command | Exit | Result |
|---|---|---|
| `DOCKER_HOST=tcp://127.0.0.1:1 uv run --no-sync pytest -q --no-header -n 8 -p no:cacheprovider` | 0 | 11093 passed, 4 skipped |
| `npx vitest run` (dashboard) | 0 | 255 files, 3723 tests passed |
| `npx tsc --noEmit -p .` (dashboard) | 2 | 0 errors in tracked files; the only errors are in the untracked review scratch dirs `__review_20260922__/` (pre-existing, not part of the repo) |
