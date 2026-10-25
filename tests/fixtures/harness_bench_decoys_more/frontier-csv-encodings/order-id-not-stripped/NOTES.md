The reference with one change: the order id is used as it is in the export, so an id with
spaces around it keeps them in orders.csv and is not the same order as the bare id. Breaks
README.md (orders.csv): "order_id — as in the export, without spaces around it."

The folder also runs the reference `_solve.py` (`python consolidate.py`), so orders.csv and
summary.json are left in the folder as the task asks.
