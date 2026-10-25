Correct, written another way: charge() is one while-loop with an attempts counter (no _post); the invoice keeps a random key ON its record (Invoice.charge_key, made before its first charge), so a run that is run again sends the same key.
Keeps every rule of billing/server_api.md and the invoices.py docstring.
