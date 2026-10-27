A BLIND attempt: a separate model (a Claude Code subagent on Sonnet) did the
task from task.md and the repo alone, without seeing the hidden check or any
fixture, while the task was written. A correct solution written another way
by another author: the list written by reading the 72 files by hand (the script it used to write
the JSON lives outside the folder). Its judgement calls match task.md:
wrappers credited to their callers, the helpers' and the decorator's own
calls left out, `rotate` in the audit package listed, the counters, methods
and locals called record, comments and the HOOKS table left out.
The hidden check must pass it.
