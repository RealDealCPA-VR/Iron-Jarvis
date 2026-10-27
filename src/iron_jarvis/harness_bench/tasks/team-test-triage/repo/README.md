# storekit

The small helper packages behind our online shop's back office.

| Package | What it does |
|---|---|
| `money/` | taxes on invoice lines (`tax.py`), invoice totals (`invoice.py`), writing amounts (`currency.py`) |
| `inventory/` | stock on hand and reservations (`stock.py`), order cancellation (`orders.py`), the catalog (`catalog.py`) |
| `scheduling/` | monthly billing dates (`billing.py`), business days (`business_days.py`) |
| `textkit/` | URL slugs (`slug.py`), shortening text (`truncate.py`) |

Each function's docstring is its contract. Amounts are `int` cents.

Run the tests from this folder:

    python -m unittest discover -s . -p "test_*.py"
