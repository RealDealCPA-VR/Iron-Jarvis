"""A task's DELIVERABLES, its WRITE SCOPE, and the check before a reply ends
(v1.481.0).

THE LIVE CASE THIS EXISTS FOR (2026-11-12, a plain chat on the user's own model
server): the user's message listed three required files under
``## Success criteria (deliverables)`` and said "CREATE files only in
`OUTPUT/`"; the chat's folder was the task's ``INPUT`` folder. The model:

* wrote its scratch scripts with ``write_file`` to ``OUTPUT/_x.py``, which
  resolved against the chat's folder: a stray ``INPUT\\OUTPUT\\`` appeared
  inside the folder that was meant to be read-only;
* wrote a helper script (``import base``) to produce two of the three files and
  never ran it; and
* ended on "The repair is complete and verified", saying one of the two missing
  files was "written".

Three parts, each a fact Iron Jarvis checks itself, never the model's word:

* :mod:`.scope` reads the user's own message (never a model's text) for the
  required files, the folders the task may write, and the folders it must only
  read; resolves them against the TASK folder; and gives every tool call of the
  turn a :class:`~.scope.TaskScope`. The tool gate (``tools/registry``)
  refuses a write outside the allowed folders, and a helper script in a
  deliverable folder, with a scratch folder outside the task to use instead.
* :mod:`.check` looks at each required file on disk (exists, not empty, opens),
  and compares the task folder with what it held when the turn began.
* :mod:`.steps` keeps the turn's failed steps (a non-zero exit, a traceback, an
  ImportError) and the scripts written and never run.

The chat lanes ask the model ONCE to finish before ending, and append a note
built from those three facts: what is done, what failed, what is not done.
"""

from .scope import TaskScope, scope_for_turn, current_scope, using_scope  # noqa: F401
