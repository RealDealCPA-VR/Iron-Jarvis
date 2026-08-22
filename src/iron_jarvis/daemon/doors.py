"""Doors — honest links into the surface a chat turn just changed (v1.199.0).

The product thesis is ONE chat surface; the app's other pages are rooms the
assistant walks you into. When a turn CREATES something that lives on another
surface (a workflow, a schedule, a memory, an agent, a custom tool), the reply
carries a DOOR into that surface: ``{"href": ..., "label": ...}``, rendered by
the dashboard under the reply.

Honesty posture (the v1.165.0 route/TurnReceipt rule): a door is derived ONLY
from a tool call that actually EXECUTED OK. The call sites in both chat lanes
sit inside the same ``if ran:`` block that appends to ``tools_used`` — a
failed, denied, or never-made call can never mint a door, because the gate is
the call site, not this module. Files written by tools are EXCLUDED on
purpose: the ArtifactsRail already owns files (``documents`` +
``created_paths``), and a duplicate door is clutter.

Hrefs are PAGE-LEVEL paths only (plus the real ``?scope=`` params the
/memory page parses — see ``dashboard/lib/nav.ts`` and
``dashboard/app/memory/page.tsx``). Entity ids stay OUT of hrefs: no target
page parses them yet, and a deep link that lands nowhere is worse than a
page-level one. When a tool's ``ToolResult.data`` reliably carries the human
name of what it made, the LABEL (never the href) is enriched with it.

MIRROR NOTE (lock-step): both chat lanes — ``chat_turn.py`` (POST /chat) and
``routes/chat.py`` (POST /chat/stream) — collect doors at their ``if ran:``
site and emit ``"doors"`` next to ``tools_used``. Edit both or neither; a
door in one lane only is the exact v1.144.0-class bug this repo documents.
"""

from __future__ import annotations

from typing import Any

#: Hard cap on doors per turn — past this the reply reads as a link farm.
MAX_DOORS = 4

#: Longest interpolated entity name in a label. Names are stored VERBATIM by
#: their tools (the saved-workflows block learned this the hard way), so a
#: hostile or clumsy name is flattened + clipped, never trusted.
_NAME_CHARS = 60

#: tool name -> door spec. ``label`` is the plain label; ``named`` (optional)
#: is the format used when ``data[name_key]`` carries the entity's human name.
#: EVERY key here is verified against the live registry by
#: ``tests/test_doors_v1199.py`` (the drift guard) — a name the registry does
#: not hold is dead code at best and a silent no-show at worst.
_CATALOG: dict[str, dict[str, str]] = {
    "workflow_create": {
        "href": "/workflows",
        "label": "Open the canvas — your workflow is saved there",
        "named": "Open the canvas — '{name}' is saved there",
        "name_key": "name",
    },
    "schedule_create": {
        "href": "/schedules",
        "label": "See your schedule",
        "named": "See your schedule — '{name}'",
        "name_key": "name",
    },
    "webhook_add": {
        "href": "/webhooks",
        "label": "See your webhook",
        "named": "See your webhook — '{name}'",
        "name_key": "slug",
    },
    # ltm_append's data is {ref, source} — a connector reference, not a human
    # title — and remember_preference's is {id, weight, scope}. Neither
    # reliably carries a name, so neither label is enriched.
    "ltm_append": {
        "href": "/memory?scope=longterm",
        "label": "See what it remembered",
    },
    "remember_preference": {
        "href": "/memory?scope=lessons",
        "label": "See the lesson it saved",
    },
    # create_agent is the PERSISTENT registration (agents/agent_tools.py) —
    # spawn_agent, the ephemeral run, deliberately has no door.
    "create_agent": {
        "href": "/agents",
        "label": "Meet your new agent",
        "named": "Meet your new agent — '{name}'",
        "name_key": "name",
    },
    "tool_create": {
        "href": "/tools",
        "label": "See your new tool",
        "named": "See your new tool — '{name}'",
        "name_key": "name",
    },
}


def _clean_name(raw: Any) -> str:
    """One physical line, bounded — a stored name is not trusted layout."""
    flat = " ".join(str(raw).split())
    if len(flat) > _NAME_CHARS:
        flat = flat[: _NAME_CHARS - 1] + "…"
    return flat


def door_for(tool_name: str, result: Any) -> dict[str, str] | None:
    """The door a SUCCESSFUL ``tool_name`` call opens, or ``None``.

    The caller guarantees success (this is called from the ``if ran:`` block
    that also appends to ``tools_used``); this function only maps the name and
    optionally enriches the label from ``result.data``. Never raises — a door
    is decoration, and no decoration is worth failing the turn over.
    """
    spec = _CATALOG.get(tool_name)
    if spec is None:
        return None
    label = spec["label"]
    named = spec.get("named")
    if named:
        try:
            data = getattr(result, "data", None) or {}
            raw = data.get(spec["name_key"]) if isinstance(data, dict) else None
            name = _clean_name(raw) if raw is not None else ""
            if name:
                label = named.format(name=name)
        except Exception:  # noqa: BLE001 — plain label beats no reply
            pass
    return {"href": spec["href"], "label": label}


def collect_doors(entries: Any) -> list[dict[str, str]]:
    """Dedupe by href (first seen wins, order preserved), cap at MAX_DOORS.

    Accepts the raw per-turn list the lanes accumulate — ``None`` entries
    (calls that opened no door) are skipped, so call sites can append
    ``door_for(...)`` unconditionally.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        href = entry.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(entry)
        if len(out) >= MAX_DOORS:
            break
    return out
