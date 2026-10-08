# Calm UI Redesign — Phase 2 REPORT (in progress)

## Test files edited (non-negotiable 7: path/route updates only)

| File | Edit | Why |
|---|---|---|
| `tests/test_browser_settings_v1235.py` | `test_browser_is_named_in_the_live_rearm_groups` reads `settings/writer.py` (`_REARM_GROUPS`) instead of `routes/settings.py` | S2 moved the live re-arm table into the one settings writer (path update for relocated code; the assertion is unchanged: `browser` is a re-armed group) |
