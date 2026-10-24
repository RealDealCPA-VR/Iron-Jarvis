The reference with one change: the rates file gets its own short reader: cells zipped to the
header (a long row is silently cut), no missing / unknown / duplicate column check, plain
utf-8 (a byte-order mark breaks the header). Breaks SPEC.md 7.5: "read with the same rules
as a transactions file (header in any case and any order, skipped rows, stripped cells, short
and long rows, line numbers)" and section 3 (byte-order mark, header errors).
