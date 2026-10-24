The reference with one change: the customer is cleaned with `" ".join(name.split())`, which
also turns two spaces inside a name into one. Breaks README.md (orders.csv): "customer — as
in the export, except that every line break inside the name becomes one space, and spaces at
the start and the end are removed."

The folder also runs the reference `_solve.py` (`python consolidate.py`), so orders.csv and
summary.json are left in the folder as the task asks.
