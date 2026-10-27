# Benchmark task generators

The scripts that wrote the generated parts of the harness benchmark's `team` tier (v1.400.0). They are
for authoring only: nothing here ships with the app or runs in a benchmark, and pytest does not collect
this folder. `tests/test_harness_bench_team_generators_v1400.py` runs each one into a scratch folder and
checks the output is byte-identical to what is committed.

- `team_audit_events.py` — team-audit-events: the sixty area modules of `shop/`, `shop/audit/events.py`,
  and the hidden check's answer key (`EXPECTED`, recorded as each write is generated) and tree digest.
- `team_review_patch.py` — team-review-patch: the colleague's format-patch file (from the old project and
  the change in `review_patch/after/`), and the solution, decoy, alternative and extra decoys merged from it
  (steps in `review_patch/`).
