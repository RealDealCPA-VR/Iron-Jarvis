# Calm UI Redesign — Phase 2 REPORT (in progress)

## Test files edited (non-negotiable 7: path/route updates only)

| File | Edit | Why |
|---|---|---|
| `tests/test_browser_settings_v1235.py` | `test_browser_is_named_in_the_live_rearm_groups` reads `settings/writer.py` (`_REARM_GROUPS`) instead of `routes/settings.py` | S2 moved the live re-arm table into the one settings writer (path update for relocated code; the assertion is unchanged: `browser` is a re-armed group) |
| `tests/test_roster_coverage_v1178.py` | **Contract extension (not a path update — flagged for VR):** the five settings tools added to `_OFF_ROSTER_BY_DESIGN` with the reason | The test's own instruction for a new chat-only tool ("add it ... WITH the reason it is deliberately unreachable"); the settings tools are chat-only by design, like the browser tools beside them. No assertion was weakened. |
| `tests/test_chat_turn_service.py` | **Contract extension (flagged for VR):** `config_cards` added to the exact response-key set, with the note the file keeps for every new key | The test is the registry of the chat response shape and documents each additive key; the new key is pinned by `test_settings_chat_tools_v1321.py`. |

## Plan steps (running log)

| Step | Commit | What landed | Proof |
|---|---|---|---|
| S0 | 6590d7a | `ui.page_opened` visits (`POST /ui/visit`, `GET /ui/visits`) + route beacon | `test_ui_visits_v1321.py`, `route-visit-v1321.test.tsx` |
| S1 | a1c3739 | ONE settings schema (`settings/schema.py`), `GET /settings/schema` | `test_settings_schema_v1321.py` |
| S2 | cefea51 | ONE writer (`settings/writer.py`): validation all-or-nothing, side effects, ledger row + undo; every settings route converted; per-device store | `test_settings_writer_v1321.py` (10 tests, 7 mutations all red) |
| S3+S4 | (this commit) | Chat settings tools generated from the schema (`config_list/set/change/change_protected/secret`), per-key tiers enforced in the tool, protected changes carded even in Auto-approve and never "Always"; "Setting changed: old → new [Undo]" cards in both chat lanes; credential card posting straight to `POST /config/secret` (names-only ledger, encrypted undo backup); composer secret-paste guard (Q9); `secret_set` armed in chat is swapped for the card; the chat sends its device id | backend `test_settings_chat_tools_v1321.py` (12 tests; 9 mutations all red), dashboard `chat-config-cards-v1321.test.tsx` (12 tests; 10 mutations all red) |

## Commands run (latest)

| Command | Exit | Result |
|---|---|---|
| `DOCKER_HOST=tcp://127.0.0.1:1 uv run --no-sync pytest -q --no-header -n 8 -p no:cacheprovider` | 0 | 11082 passed, 4 skipped |
| `npx vitest run` (dashboard) | 0 | 255 files, 3720 tests passed |
| `npx tsc --noEmit -p .` (dashboard) | 2 | 0 errors in tracked files; the only errors are in the untracked review scratch dirs `__review_20260922__/` (pre-existing, not part of the repo) |
