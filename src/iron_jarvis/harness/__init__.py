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

Phase 3 (v1.366.0) makes the harness an explicit, per-family system:

* :mod:`.dialects` — the dialect table: the tool names and argument shapes
  each model family was trained on (Claude Code's, Codex's, Qwen Code's,
  Jarvis's own), translated onto the real tools at the boundary.
* :mod:`.components` — the eight components (Harness²) as one tree, layered
  default → family → the project folder's ``.ironjarvis/harness/``,
  validated, rendered and fingerprinted.
* :mod:`.mode` — HARNESS MODE for a chat turn (``harness=`` on the two chat
  lanes): the coding tool set, the dialect, the tree, no escalation, one
  nudge for a tool call written as text.
"""
