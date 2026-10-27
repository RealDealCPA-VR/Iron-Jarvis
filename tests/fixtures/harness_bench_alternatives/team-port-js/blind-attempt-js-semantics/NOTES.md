A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written. A correct solution written another way
by another author: a shared `strutil/_js.py`, toFixed on the exact value, a strict
decodeURIComponent, UTF-16 comparison and lengths (it also orders integer
keys first and drops a `__proto__` key, as a JavaScript object would, which
the check does not ask about), and a `test_extra.py` of its own.
The hidden check must pass it.
