A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written (53 seconds, seven tool calls). A correct
solution written another way by another author: the thirteen causes fixed in
its own words (Decimal tax rounding half away from zero, release through
sku_key, order_day converted to UTC, billing from the anchor day, NFKD slugs,
one exact sum rounded up for the parcel, the user name matched in any case,
quiet hours past midnight with start == end meaning none, the Luhn check
counted from the rightmost digit, the last weekday from the month's real last
day, interpolated percentiles, a promotion that leaves the price list alone,
the importer reading Excel's byte order mark); no test file touched.
The hidden check must pass it.
