# Order exports

Every branch office sends its orders as CSV files; they land in `exports/`.
`consolidate.py` turns all of them into one clean file for accounting,
`orders.csv`, plus a `summary.json`. The files in `exports/` are what the
offices sent: never change them.

## Running it

    python consolidate.py                # reads exports/, writes orders.csv and summary.json next to consolidate.py
    python consolidate.py EXPORTS OUT    # reads the folder EXPORTS, writes into the folder OUT (made if missing)

From Python, `consolidate(exports_dir, out_dir)` does the same and returns the
summary (the dict it writes to `summary.json`).

## The export files

- The name is `<office>_<YYYY>-<MM>.csv`, and an export an office sent again is
  `<office>_<YYYY>-<MM>_resend.csv`. The office is the part of the name before
  the first `_` (`berlin_2026-03.csv` comes from `berlin`). Read the files in
  name order.
- Every file starts with a header row. Read the columns BY NAME: the offices
  put them in different orders, and some add columns that accounting does not
  use (ignore those). The three columns that matter:

  | Office                                  | order id        | customer   | amount     |
  |-----------------------------------------|-----------------|------------|------------|
  | berlin                                  | `Bestellnummer` | `Kunde`    | `Betrag`   |
  | every other office                      | `order_id`      | `customer` | `amount`   |

- Encoding — decide it per FILE (London changed its system in the middle of
  the year, so one office can send both):
  - a file that starts with the bytes `FF FE` is UTF-16, little-endian (those
    two bytes are the byte order mark, not text);
  - a file that starts with the bytes `EF BB BF` is UTF-8 (those three bytes
    are the byte order mark: they are not part of the first column's name);
  - any other file is UTF-8 if all of it decodes as UTF-8, and Windows-1252
    (cp1252) if it does not.
- Line endings can be CRLF or LF, in any file. The result must not depend on
  them.
- The delimiter is `;` in Berlin's files and `,` in every other office's
  files. Fields may be quoted with `"`; a quoted field can contain the
  delimiter, a doubled quote `""` (meaning one `"`) and line breaks.
- Amounts may have spaces around them. An amount is: an optional `-`, then
  digits — optionally grouped in threes by the office's thousands separator —
  then optionally the office's decimal point followed by one or two digits.
  Nothing else is an amount.
  - Berlin writes amounts the German way: `.` separates thousands and `,` is
    the decimal point (`1.234,50` is 1234.50, `2.500` is 2500.00, `-12,5` is
    -12.50).
  - Every other office: `.` is the decimal point and `,` separates thousands
    (`1,234.50` is 1234.50, `1,234` is 1234.00, `12.5` is 12.50).

## Bad rows and duplicates

- A blank line (nothing at all on it) is not a row: ignore it.
- A row is BAD when it does not have exactly as many fields as the header,
  when its order id is empty, or when its amount is not an amount (above).
  Skip it, and count it in `skipped_rows`.
- An office sometimes sends the same order twice (a resent export repeats
  orders of the original). An order id that was already seen in an earlier
  good row (files in name order, rows in file order) is a DUPLICATE: keep the
  first one, skip the copy, and count it in `duplicates`. A bad row is never a
  duplicate and never makes one.

## orders.csv

- UTF-8 without a byte order mark, LF line endings (`\n`, never `\r\n`).
- The header row `order_id,office,customer,amount`, then one row per order,
  sorted by `order_id` (plain text order).
- `order_id` — as in the export, without spaces around it.
- `office` — the office name from the file name (lower case).
- `customer` — as in the export, except that every line break inside the
  name (`\r\n`, `\n` or `\r`) becomes one space, and spaces at the start and
  the end are removed.
- `amount` — with `.` as the decimal point, exactly two decimals, no
  thousands separator, `-` for a negative amount: `1234.50`, `-12.50`,
  `2500.00`.
- Written the way Python's `csv.writer(file, lineterminator="\n")` writes
  (with the file opened with `newline=""`): a field is quoted only when it
  has to be (it contains a comma, a quote or a line break), and a quote
  inside a quoted field is doubled.

## summary.json

For example (the numbers here are made up):

    {
      "offices": {
        "berlin": {"orders": 12, "total": "12345.60"},
        "boston": {"orders": 9, "total": "-20.00"}
      },
      "skipped_rows": 3,
      "duplicates": 2
    }

- `offices` — one entry for every office that has at least one order in
  `orders.csv`: `orders` is how many rows of `orders.csv` belong to it, and
  `total` is the exact sum of their amounts as a string with two decimals.
- `skipped_rows` — how many bad rows were skipped, in all files together.
- `duplicates` — how many duplicate rows were skipped, in all files together.
- Any JSON layout (indentation, key order) is fine.
