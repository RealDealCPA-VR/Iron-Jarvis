A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written. A correct solution written another way
by another author: its own reading where SPEC.md is open: an empty comparator set is an
error, a leading v is accepted in ranges, `~1.x`/`^1.x` read as `~1`/`^1`,
and a wrong-typed `n` for split_evenly raises an error that is both a
ValueError and a TypeError.
The hidden check must pass it.
