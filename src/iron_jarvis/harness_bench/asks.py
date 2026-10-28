"""How often a benchmark run WOULD HAVE ASKED the user (v1.402.0, the
autonomy metrics).

The benchmark answers every card itself ("Don't ask" + the runner's own
answer — ``facts.asks`` counts those), so its interruptions are zero by
construction. To say what a person sitting there would have been asked, each
tool call of a run is REPLAYED through the real permission engine under the
chat/Build lane's DEFAULT posture (``approve_for_me``) with no grant of any
kind — ``eval.autonomy.would_card``, the lane's own rule (pinned against the
real lane in ``tests/test_autonomy_metrics_v1402.py``).

Every runner's calls are replayed:

* Jarvis — its trajectory names the REAL tools (the lane translates a
  dialect's names at the boundary), so they are judged as they are; a name
  the registry does not hold is one the lane refuses without a card.
* Claude Code — its ``tool_use`` names are mapped through the Claude dialect
  (``harness.dialects.CLAUDE``: ``Bash`` → ``shell``, a background ``Bash`` →
  ``process_start``, ``Write`` → ``write_file`` …), else the parity table
  (``harness.parity.CLAUDE_CODE``).
* Codex — ``codex exec --json`` reports item KINDS, not tool names; each
  kind is mapped through the parity table's Codex row (``command_execution``
  → ``exec_command`` → ``shell``, ``file_change`` → ``apply_patch`` …).

A name that cannot be mapped (an app's tool, a vendor tool Jarvis does not
serve) is counted as ``not known`` — never guessed either way.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Codex ``item.completed`` kinds that are a tool → the parity table's key.
CODEX_ITEM_TOOLS = {
    "command_execution": "exec_command",
    "file_change": "apply_patch",
    "web_search": "web_search",
    "collab_tool_call": "collaboration.spawn_agent (v2)",
    "mcp_tool_call": "mcp__<server>__<tool>",
}

#: At most this many calls are replayed per run (a runaway run is bounded).
MAX_CALLS = 2000


def default_permissions() -> Any:
    """The permission engine a fresh install starts with — what a CLI
    runner's calls are judged by (it has no platform of its own)."""
    from ..core.config import default_permissions as _table
    from ..tools.permissions import PermissionEngine

    return PermissionEngine(_table())


def _served(table: dict, key: str) -> str:
    row = table.get(key)
    if row is None:
        return ""
    from ..harness.parity import APP_TOOL_PATTERN, SERVED

    if row.verdict != SERVED or not row.jarvis or row.jarvis[0] == APP_TOOL_PATTERN:
        return ""
    return row.jarvis[0]


def claude_tool(name: str, args: Any = None) -> str:
    """The Jarvis tool a Claude Code call maps to, or ``""`` (not known)."""
    name = str(name or "")
    if not name or name.startswith("mcp__"):
        return ""
    from ..harness import dialects
    from ..harness.mode import CODING_TOOLS

    tool = dialects.CLAUDE.lookup(name)
    if tool is not None:
        try:
            real, _ = tool.translate(dict(args or {}), frozenset(CODING_TOOLS))
            if real:
                return str(real)
        except Exception:  # noqa: BLE001 — a translate never raises; belt and braces
            pass
    from ..harness.parity import CLAUDE_CODE

    return _served(CLAUDE_CODE, name)


def codex_tool(kind: str) -> str:
    """The Jarvis tool a Codex item kind maps to, or ``""`` (not known)."""
    key = CODEX_ITEM_TOOLS.get(str(kind or ""))
    if not key:
        return ""
    from ..harness.parity import CODEX

    return _served(CODEX, key)


def would_have_asked(
    calls: Iterable[tuple[str, Any]], *, runner: str, permissions: Any = None, registry: Any = None,
) -> dict[str, Any]:
    """``{calls, asked, not_known, by_tool}`` for one run. ``calls`` are
    ``(name, args)`` in the runner's own vocabulary (``runner``: ``jarvis`` |
    ``claude`` | ``codex``); ``by_tool`` counts the asks per JARVIS tool."""
    from ..eval.autonomy import would_card

    perms = permissions if permissions is not None else default_permissions()
    asked = not_known = total = 0
    by_tool: dict[str, int] = {}
    for name, args in calls:
        if total >= MAX_CALLS:
            break
        total += 1
        if runner == "claude":
            real = claude_tool(name, args)
        elif runner == "codex":
            real = codex_tool(name)
        else:
            real = str(name or "")
            if registry is not None and real:
                try:
                    if registry.get(real) is None:
                        # The lane refuses a name it does not hold — no card.
                        continue
                except Exception:  # noqa: BLE001
                    pass
        if not real:
            not_known += 1
            continue
        # Every runner is judged as a coding session's turn — how the
        # benchmark runs Jarvis — so the three read alike.
        verdict = would_card(real, permissions=perms, registry=registry, harness=True)
        if verdict is None:
            not_known += 1
        elif verdict:
            asked += 1
            by_tool[real] = by_tool.get(real, 0) + 1
    return {"calls": total, "asked": asked, "not_known": not_known, "by_tool": by_tool}


def calls_of(outcome: Any, trajectory: Any = None) -> tuple[str, list[tuple[str, Any]]]:
    """``(runner vocabulary, calls)`` from a run's outcome: a Jarvis run's
    trajectory, a Claude Code run's ``tool_uses``, a Codex run's
    ``tool_items``. ``("", [])`` when the run recorded none of them."""
    extra = getattr(outcome, "extra", None) or {}
    if isinstance(trajectory, list):
        return "jarvis", [
            (str(e.get("tool") or ""), e.get("args"))
            for e in trajectory
            if isinstance(e, dict) and e.get("kind") == "call"
        ]
    if isinstance(extra.get("tool_uses"), list):
        return "claude", [
            (str(u.get("name") or ""), u.get("input"))
            for u in extra["tool_uses"] if isinstance(u, dict)
        ]
    if isinstance(extra.get("tool_items"), list):
        return "codex", [(str(k or ""), None) for k in extra["tool_items"]]
    return "", []


__all__ = [
    "CODEX_ITEM_TOOLS", "calls_of", "claude_tool", "codex_tool", "default_permissions", "would_have_asked",
]
