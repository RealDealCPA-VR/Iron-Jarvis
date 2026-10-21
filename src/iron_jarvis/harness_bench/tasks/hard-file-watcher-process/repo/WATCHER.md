# The inbox watcher

Start it from this folder with `python watcher.py`. It runs until it is told
to stop.

## What it does

- On start it picks a new run id (16 hex characters), appends the line
  `started <run id>` to `watcher.log`, and writes `results.json` as
  `{"run": "<run id>", "files": {}}`.
- About five times a second it looks in `inbox/`. Every file there whose name
  ends in `.csv` is processed, in name order: it is totalled (below), its
  entry is added to `results.json` under `"files"`, it is moved to `done/`,
  and the line `processed <file name>` is appended to `watcher.log`.
- A file named `STOP` in `inbox/` tells it to stop. Before it stops it FIRST
  processes every `.csv` file that is in `inbox/` at that moment, so nothing
  that arrived with the STOP is left behind. Then it deletes `inbox/STOP`,
  appends the line `stopped` to `watcher.log`, and exits.

## Totalling a file

Each CSV file has a header row with an `amount` column (and other columns that
do not matter). Amounts are money: whole numbers or numbers with up to two
decimals, possibly negative, possibly with spaces around them (`" 12.50 "`).

The entry for a file is:

```json
{"rows": 3, "total": "27.40", "bad_rows": 1}
```

- `rows` — the number of data rows (not counting the header);
- `total` — the exact sum of the amounts that are numbers, as a string with
  exactly two decimals (`"27.40"`, `"-3.00"`, `"0.00"`) — no rounding
  errors;
- `bad_rows` — how many rows had an amount that is not a number (for example
  `n/a` or nothing at all). Those rows are left out of the total. A bad row
  never stops the watcher.

`total_file(path)` returns this entry for one file.
