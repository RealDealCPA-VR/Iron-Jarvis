The reference with one change: a rates file that is not there is read as an empty table, so
`report CSV --base USD` without a rates file ends in a MissingRate (exit 2).
Breaks SPEC.md 7.7: "Every other error keeps exit code 1 (a rates file that cannot be read
or holds a ParseError too)."
