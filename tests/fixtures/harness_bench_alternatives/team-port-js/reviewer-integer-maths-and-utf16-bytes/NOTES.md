Reviewer's alternative (v1.400.0 review), written without looking at the reference solution or the
other alternatives: `toFixed` by exact integer maths on the float's `as_integer_ratio` (no `round`,
`format` or `Decimal`); `decodeURIComponent` by hand (hex pairs, strict UTF-8, the whole component kept
on any failure); JavaScript's string order by comparing UTF-16 big-endian bytes; `wrapText` on lists of
UTF-16 code units; `titleCase` with JavaScript's `\s` spelled out as a set and no regular expressions.
It must PASS the hidden check.
