# A second held-out set for the capability search plan (Phase 2)

See `docs/CAPABILITY-SEARCH-PLAN-2026-11-12.md` and
`tests/test_capability_join_v1479.py`.

- `requests.json`: 104 requests (94 English, 10 Italian) for the same 47
  kinds of request as `../capability_heldout_v1478/intents.json`. Written on
  2026-11-12 by Claude Haiku run from an empty folder, with six NEW writer
  personas (none used for the first set) and Italian as a new language. It
  never saw this repository or the first set. An e-mail address in one row was
  replaced with `billing@example.com`.
- `queries.json`: the search words a second blind Haiku wrote for each
  request, shown only the lookup tool's description and the message.

**The labels were not checked by hand** (the first set's were). The set was
made for ONE comparison: the lookup's answer order with and without the
"a tool a lookup can bring comes first" rule, which had been written before
this set existed. Read as an absolute score it is rougher than the first set.

**This set is held out too.** It was used once, to validate that rule. Do
not add or change a pattern, an index entry or a tool description because a
row here misses.
