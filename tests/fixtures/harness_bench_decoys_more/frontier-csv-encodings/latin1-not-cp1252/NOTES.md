The reference with one change: a file that is not UTF-8 is decoded as latin-1, not cp1252, so
the euro sign, curly quotes and dashes (bytes 0x80-0x9F) come out as control characters.
Breaks README.md (Encoding): "any other file is UTF-8 if all of it decodes as UTF-8, and
Windows-1252 (cp1252) if it does not."

The folder also runs the reference `_solve.py` (`python consolidate.py`), so orders.csv and
summary.json are left in the folder as the task asks.
