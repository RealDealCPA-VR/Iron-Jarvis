"""Claude via the user's logged-in Claude Code CLI, spoken NATIVELY (v1.300.0).

Rebuilt on the design of NousResearch's MIT-licensed
``hermes-plugin-claude-subscription-directsdk`` (commit ef73726): the CLI is
driven as a request-scoped model client over ``--input-format stream-json``.

* ``frames``     — our transcript -> native stream-json frames (history replay
  with ``shouldQuery: false``, signed-thinking carriers), tool-name mapping and
  input-schema normalisation. Pure, no I/O.
* ``transport``  — one CLI process per request: private files, the child env
  and its conflict guard, the admission relay, frame writing, the stdout
  reader, timeouts, cancellation (the process TREE dies), final assembly.
* ``inert_mcp``  — the tool INVENTORY the CLI sees. It answers tools/list and
  refuses every tools/call: Iron Jarvis executes tools, never the CLI.
* ``admission``  — the loopback relay that admits exactly one upstream
  Messages request per call (another doer's module).

KEEP THIS FILE IMPORT-FREE. ``python -m ...claude_native.inert_mcp`` imports
this package first, once per request, inside the CLI's startup path; anything
heavy here is paid on every model call.
"""

from __future__ import annotations

#: The ``raw_blocks`` entry type this adapter writes (and only it reads).
CARRIER = "claude_cli_native"

__all__ = ["CARRIER"]
