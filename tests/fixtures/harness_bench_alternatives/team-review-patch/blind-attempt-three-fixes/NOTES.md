A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written (47 seconds, five tool calls). A correct
solution written another way by another author: the whole patch applied, the
three edits fixed in its own words (the cap on the sum of coupon and tier,
points on whole dollars, gold's free shipping from exactly $50.00), a
`ReviewFixesTest` class added to test_loyalty.py, and one change of its own in
cli.py: an explicit `--tier ""` now means bronze instead of being ignored (the
description does not say; the check does not ask).
The hidden check must pass it.
