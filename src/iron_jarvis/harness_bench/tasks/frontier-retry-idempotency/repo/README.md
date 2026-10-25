# billing

- `billing/` — the payments client (`PaymentsClient.charge`), its errors, and
  the transport it talks through. `billing/server_api.md` describes the
  payments server's API.
- `invoices.py` — the monthly invoice run (`run_invoices`), which charges a
  batch of invoices with the client.

Run the tests (Python 3.12, standard library only):

    python -m unittest discover -s . -p "test_*.py"
