# shop

The back end of our online shop: one package per area under `shop/`
(`orders`, `payments`, `inventory`, `accounts`, `shipping`, `support`,
`catalog`, `reports`), each a handful of modules.

Two things record what happens, and they are NOT the same:

* the AUDIT TRAIL, `shop/audit/` — what changed money, stock or access, kept
  for the auditors (see `docs/audit.md`);
* the dashboard COUNTERS, `shop/metrics.py` — numbers for the graphs, never
  kept. Its function is also called `record`.
