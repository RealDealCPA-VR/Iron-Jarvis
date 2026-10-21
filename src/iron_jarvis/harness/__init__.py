"""The IJ harness (v1.364.0 onward): the coding tools Claude Code and Codex
give their models, built on Jarvis's real tool registry.

Phase 1 (``docs/IJ-HARNESS-PLAN.md``) is the TOOL SET: every capability the
two vendor CLIs ship is served by a Jarvis tool or recorded as not needed
(:mod:`iron_jarvis.harness.parity`). The tools register in the platform
registry like any other built-in; no chat lane and no agent roster arms them
by themselves — the IJ harness session (Phase 3/4) arms them by name.

* :mod:`.textedit` — the shared exact-edit engine (strict unique match,
  ``replace_all``, line endings kept, the nearest-line hint).
* :mod:`.patch` — Codex's V4A ``apply_patch`` format: parser + all-or-nothing
  application over an in-memory view of the workspace.
* :mod:`.verify` — the fast syntax check run after a code file is written.
* :mod:`.tools` — ``multi_edit``, ``apply_patch``, ``glob``, ``plan_update``,
  ``notebook_edit``, ``mcp_list_resources`` / ``mcp_read_resource``.
* :mod:`.process` — ``process_start`` / ``process_read`` / ``process_write`` /
  ``process_stop`` over ``core.procs``.
* :mod:`.parity` — the capability table, checked against the inventory fixture.
"""
