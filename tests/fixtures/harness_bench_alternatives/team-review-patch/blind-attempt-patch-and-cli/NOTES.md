A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written. A correct solution written another way
by another author: the patch applied with `patch -p1`, the cap fixed as min(coupon + tier, 25),
two cap assertions added to test_loyalty.py, and one change of its own in
cli.py: an explicit `--tier ""` now means bronze instead of being ignored
(the description does not say; the check does not ask).
The hidden check must pass it.
