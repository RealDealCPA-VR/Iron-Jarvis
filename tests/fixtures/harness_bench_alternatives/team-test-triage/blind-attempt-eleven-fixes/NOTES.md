A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written (50 seconds, seven tool calls). A correct
solution written another way by another author: the eleven causes fixed in
its own words (Decimal tax rounding half away from zero, release through
sku_key, order_day converted to UTC, billing from the anchor day, NFKD slugs,
one exact sum rounded up for the parcel, the user name matched in any case,
quiet hours past midnight with start == end meaning none, the Luhn check
counted from the rightmost digit, the last weekday from the month's real last
day, interpolated percentiles); no test file touched.
The hidden check must pass it.
