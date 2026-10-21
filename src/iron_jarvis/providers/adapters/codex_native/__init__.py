"""Codex via the user's own ``codex app-server``, spoken NATIVELY (IJ harness phase 2).

The Codex counterpart of ``claude_native`` (v1.300.0). ``codex exec`` (the
``subprocess_cli`` text adapter) flattens the conversation into one prompt and
cannot call tools; the app-server speaks JSON-RPC over stdio and lets a client
supply its OWN tools (``dynamicTools`` on ``thread/start``). Codex runs its own
agent loop and, when the model calls one of those tools, asks the client to run
it (the ``item/tool/call`` server request) and waits for the answer.

Iron Jarvis's lanes want the opposite shape: the adapter RETURNS tool calls and
is called again with their results. The bridge (``adapter``):

* a call whose conversation ends with tool results for calls a LIVE session is
  waiting on RESUMES that session: each pending request is answered with the
  ``role="tool"`` message for its call id, and Codex streams on until it asks
  for the next tool or the turn ends;
* anything else is a NEW session: a fresh app-server process, a fresh
  ephemeral thread, the earlier conversation replayed as TEXT (honestly
  labelled) and the current ask as the turn's input.

Iron Jarvis executes every tool through ``registry.invoke`` (armed-set gate,
approvals, ledger, undo, trust). Codex's own tools that act are switched off
on the thread (``protocol.BUILTINS_OFF``, no environment) and the start-up
answer is CHECKED before the first model call; a built-in approval request
that still arrives is denied.

* ``protocol`` — pure: thread config, tool-name map, history -> input items,
  usage deltas, rate-limit snapshots, error typing. No I/O.
* ``session``  — one app-server process: spawn through ``core.procs``, a
  reader thread, JSON-RPC, the live-session pool (cap, idle reaper) and the
  feature verdict.
* ``adapter``  — :class:`CodexNativeAdapter` (``complete`` / ``stream``),
  Iron-Proxy accounts, and the text-only fallback.

Nothing here runs as a child of the frozen exe: the app-server is the user's
own ``codex`` binary, so there is no fast-path shim (claude_native's inert MCP
server needed one; this does not).
"""

from __future__ import annotations

__all__: list[str] = []
