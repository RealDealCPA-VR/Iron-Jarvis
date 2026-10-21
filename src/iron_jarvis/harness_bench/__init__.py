"""The harness benchmark (IJ harness plan, Phase 0).

A fixed set of small coding tasks (``tasks/``), three runners — Iron Jarvis's
own chat-thread turn, Claude Code (``claude -p``) and Codex (``codex exec``) —
each working in a fresh copy of the task, graded afterwards by a HIDDEN check
in a separate copy, with every model call in a budget ledger and every
finished run under a completion marker that names the harness that produced
it. ``ironjarvis bench-harness run|report`` is the command line.

Modules: :mod:`.catalog` (tasks), :mod:`.tripwire` (the grader stays out of
the agent's reach), :mod:`.grading`, :mod:`.ledger`, :mod:`.runners`,
:mod:`.bench` (one configuration, its folder and its resume rules) and
:mod:`.report`. Nothing is re-exported here on purpose: a package that
re-exports a function shadows the module of the same name.
"""
