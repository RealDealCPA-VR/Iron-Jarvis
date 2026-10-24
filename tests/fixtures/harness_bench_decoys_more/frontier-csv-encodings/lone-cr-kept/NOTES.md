The reference with one change: only `\r\n` and `\n` inside a customer name become a space; a
lone `\r` stays. Breaks README.md (orders.csv): "every line break inside the name (`\r\n`,
`\n` or `\r`) becomes one space".

The folder also runs the reference `_solve.py` (`python consolidate.py`), so orders.csv and
summary.json are left in the folder as the task asks.
