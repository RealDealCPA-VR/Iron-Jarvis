# Report formats

A report has a title, column names, rows (one value per column) and notes.
Every exporter is a function `report -> str` registered in
`reporter/exporters/` under a name, with the extension its files get.
`reporter.exporters.get(name)` accepts a name or an alias.
`reporter/formatting.py` decides how a value is written as text
(`format_cell`) and whether a column is numeric (`is_numeric_column`); the
formats below that write formatted values all use it, so a value looks the
same in every format.

## csv — `.csv`

The column names, then one line per row of formatted values.

## json — `.json`

The raw values (not formatted), indented by two spaces.

## html — `.html` (also `htm`)

A heading, a table (numeric columns get `class="num"`), and one paragraph per
note.

## markdown — `.md` (also `md`)

For `samples/sales.json` the Markdown export is exactly:

```
# Quarterly sales

| Region | Units | Revenue | On target | Note |
| --- | ---: | ---: | --- | --- |
| North | 1,200 | 15,000.50 | yes | best \| ever |
| South | 30 | 99.00 | no | line one<br>line two |
| East |  | 7.25 |  |  |

> Figures are provisional.
> Ask finance before sharing.
```

The rules:

1. The first line is `# ` followed by the title; then one blank line.
2. The header row holds the column names. The separator row holds `---:` for
   a numeric column (as `formatting.is_numeric_column` decides) and `---` for
   any other.
3. Every value is written with `formatting.format_cell`. Then, in that text
   and in the column names and the title, a `|` becomes `\|`, and a line
   break (`\r\n` or `\n`) becomes `<br>`.
4. A row is `| ` + its cells joined by ` | ` + ` |`. An empty cell is just
   nothing between the separators (`|  |`).
5. A report with no rows has the header and separator rows, then one blank
   line and the line `_No rows._`.
6. When the report has notes: one blank line after the table (or after
   `_No rows._`), then each note as a quote. A note that spans several lines
   becomes one `> ` line per line. Notes are escaped like cells (rule 3)
   except that a line break starts a new `> ` line instead of becoming `<br>`.
7. The text ends with exactly one newline.

## The command line

```
python -m reporter.cli REPORT.json --format FORMAT [--out FILE]
python -m reporter.cli --list-formats
```

- `--format` accepts every registered format name and every alias (so
  `--format md` and `--format markdown` both give Markdown).
- Without `--out`, the file is written next to the report file, with the same
  name and the format's extension (`samples/sales.json` → `samples/sales.md`).
  The command prints the path it wrote.
- `--list-formats` prints one line per registered format, sorted by name:
  the name, a space, and the extension in brackets — for example
  `csv (.csv)`.
