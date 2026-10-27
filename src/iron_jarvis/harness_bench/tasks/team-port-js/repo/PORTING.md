# Porting strutil.js to Python

The web front end's string helpers live in `reference/strutil.js`. The back
end needs the same helpers in Python, in the package `strutil/` — one module
per helper. The modules exist with their signatures; every function raises
`NotImplementedError` today.

| JavaScript (`reference/strutil.js`) | Python | Module |
|---|---|---|
| `formatBytes(bytes, { decimals, binary })` | `format_bytes(value, decimals=1, binary=False)` | `strutil/sizes.py` |
| `parseQuery(query)` | `parse_query(query)` | `strutil/query.py` |
| `naturalCompare(a, b)`, `naturalSort(items)` | `natural_compare(a, b)`, `natural_sort(items)` | `strutil/natural.py` |
| `wrapText(text, width)` | `wrap_text(text, width)` | `strutil/wrap.py` |
| `titleCase(text)` | `title_case(text)` | `strutil/titles.py` |

`strutil/__init__.py` re-exports all six names.

## What "the same" means

* Every function returns what the reference returns, for every input — the
  same text, the same values, in the same order. `natural_compare` returns
  `-1`, `0` or `1`. `natural_sort` returns a new list (its argument is left
  alone). `parse_query` returns a `dict` whose values are `str` or, where the
  reference makes an array, a `list` of `str`. The options object of
  `formatBytes` becomes keyword arguments with the same defaults.
* Errors: where the reference throws a `TypeError`, raise `TypeError`; where
  it throws a `RangeError`, raise `ValueError`. The message is yours.
* The reference is JavaScript, and that matters here:
  * its numbers are 64-bit floats, and its rounding is JavaScript's own
    (`Number.prototype.toFixed`, `String(number)`);
  * its strings are JavaScript strings — sequences of UTF-16 code units: where
    the reference measures, cuts or compares strings (`length`, `slice`, `<`),
    the result must be the one JavaScript gives;
  * its regular expressions and case changes are JavaScript's (what `[0-9]`,
    `[A-Z]` and `\s` match, `toUpperCase`, `toLowerCase`, `split`).

  A port that looks the same line by line can still give other results; read
  the reference closely.
* Python 3.12, standard library only. Nothing may run the JavaScript file:
  it is kept here to read.

## Tests

`test_sizes.py`, `test_query.py`, `test_natural.py`, `test_wrap.py` and
`test_titles.py` hold a few first cases (they fail today). Do not change
them; add tests of your own in new files if you want.
