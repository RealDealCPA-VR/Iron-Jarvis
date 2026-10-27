Every audit write is found, but the dashboard counters are listed too:
`metrics.record(...)` and `record_metric(...)` calls with a literal name are
taken for writes to the audit trail. README.md and task.md both say the
counters are not the audit trail. Nothing is missing, only extras; the hidden
check must fail it.
