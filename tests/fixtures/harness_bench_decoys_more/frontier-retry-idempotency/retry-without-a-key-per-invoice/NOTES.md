The obvious fix: charge() retries with one key per call, invoices.py is untouched, so a resumed run sends a NEW key for the invoice that stopped the run.
Breaks billing/server_api.md "Idempotency" (keep the key with your own record so the resumed run sends the same key) and invoices.py ("no invoice is ever charged twice — not even the one that stopped the run").
