# Held-out requests for the capability search plan (Phase 0)

See `docs/CAPABILITY-SEARCH-PLAN-2026-11-12.md` and
`tests/test_capability_reach_v1478.py`.

- `intents.json`: 47 everyday jobs and what counts as reached. Built by
  `build_intents.py`, before any score was read.
- `requests.json`: 164 requests (135 English, 29 in Spanish, French,
  Portuguese and German). Written by Claude Haiku run from an empty folder,
  given only the plain job descriptions and a persona per batch. It never saw
  this repository. Every label was checked by hand; 7 rows that could as well
  mean another job were dropped.
- `near_misses.json`: 99 messages that use the same everyday words and ask
  for nothing to be changed. 2 rows that did ask for something were dropped.

**This set is held out.** Do not add or change a pattern because a row here
misses. A miss is the measurement.
