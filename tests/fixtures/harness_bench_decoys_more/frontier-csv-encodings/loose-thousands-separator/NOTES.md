The reference with one change: thousands separators are removed wherever they are before the
amount is checked, so `12,34` (Boston) and `12.34` (Berlin) read as 1234.00. Breaks README.md
(Amounts): "digits — optionally grouped in threes by the office's thousands separator ...
Nothing else is an amount."

The folder also runs the reference `_solve.py` (`python consolidate.py`), so orders.csv and
summary.json are left in the folder as the task asks.
