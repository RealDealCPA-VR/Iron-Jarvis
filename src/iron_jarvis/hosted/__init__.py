"""Build's Code view: the user's REAL Claude Code / Codex, driven from a GUI
(v1.409.0, ``docs/BUILD-APP-VIEW-PLAN.md``).

Chat's Code view runs every tool itself and switches the harness's own tools
OFF (``providers/adapters/claude_native`` / ``codex_native``). This package
is the other way round: the HARNESS runs everything itself, exactly as in a
terminal — its own tools, settings, CLAUDE.md / AGENTS.md, MCP servers,
hooks, permission rules and its own session file under the account's home.
Iron Jarvis only starts it, draws what it does (the normalized events of
:mod:`.events`), relays the user's messages and turns every permission
question the harness asks into a card the USER answers. Nothing that switches
built-ins off is reused here — only process plumbing (``core/procs``).

Modules:

* :mod:`.models` — ``HostedSessionRecord`` (a late model);
* :mod:`.store` — its blocking CRUD;
* :mod:`.events` — the event contract's helpers and the bounded
  ``events.jsonl`` log;
* :mod:`.process` — one harness child: spawn, reader threads, writes, kill;
* :mod:`.claude` / :mod:`.codex` — each harness's protocol and parser;
* :mod:`.service` — sessions, turns, asks, the pool (cap + idle reaper),
  continue on the next account, open in a terminal and back.
"""
