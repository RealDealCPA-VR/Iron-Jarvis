A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written (96 seconds, seventeen tool calls). A
correct solution written another way by another author: the hunks that did
not apply merged by hand in its own words (README, cli.py, invoice.py and
shipping.py keep express and --output), the three edits fixed (the cap on the
sum of coupon and tier, points on whole dollars, gold's free shipping from
exactly $50.00 with express still $12.99 for everyone), and tests of its own
added to test_loyalty.py.
The hidden check must pass it.
