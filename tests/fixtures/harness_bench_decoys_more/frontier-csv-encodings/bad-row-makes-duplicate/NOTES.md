The reference with one change: the duplicate check runs before the amount is checked, and an
order id is remembered as soon as it is seen, so a bad row makes a later good copy a
"duplicate" and a bad copy of a good order counts as a duplicate, not a bad row. Breaks
README.md (Bad rows and duplicates): "A bad row is never a duplicate and never makes one."

The folder also runs the reference `_solve.py` (`python consolidate.py`), so orders.csv and
summary.json are left in the folder as the task asks.
