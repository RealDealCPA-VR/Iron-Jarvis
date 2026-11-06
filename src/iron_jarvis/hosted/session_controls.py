"""The Session tab's live controls (v1.441.0, lane g1): background work a
coding tool runs, with Stop, and its own MCP servers' actions — through the
TOOL's own requests, never Iron Jarvis's.

ONE generic shape every driver can offer later:

* ``Driver.background_work()`` -> ``[{id, title, kind, can_stop}]`` or None
  (None = this driver offers no list; the overview then carries no
  ``background_work`` key, so the tab looks exactly as before);
  ``Driver.stop_background(id)`` -> the title of what stopped.
* ``Driver.mcp_actions`` (a set of ``reconnect`` | ``off`` | ``on`` |
  ``sign_in``) and ``Driver.mcp_action(server, action)`` -> ``{}`` or
  ``{"url": <a sign-in page for the user's browser>}``. Each MCP row of the
  overview gains ``actions`` — only the ones that fit its state, only while
  the tool runs.

Who offers what (each MEASURED; the driver's docstring says how):

* Claude Code 2.1.316: its open tasks (``task_started`` .. ``task_notification``)
  stopped with ``stop_task``; ``mcp_reconnect``; ``mcp_toggle`` (Claude Code
  remembers a server turned off for the folder, as its own ``/mcp`` does).
* Codex 0.157.0: its background terminals (``thread/backgroundTerminals/list``
  / ``terminate``, experimental); ``mcpServer/oauth/login`` (a sign-in page the
  user's browser opens; Iron Jarvis never signs in).

Stopping a background task never stops the reply: the tool's own stop takes
only that task (measured: no model request follows it). What was stopped is
said in the conversation as a notice.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .driver import ControlRefused
from .events import clip, make
from .tool_setup import CONNECTED, FAILED, NEEDS_SIGN_IN, OFF, STARTING

#: One request to the tool waits at most this long.
ACTION_TIMEOUT_S = 30.0
#: The background list waits at most this long inside the overview read
#: (the tab never waits on a slow tool; it shows the rest).
LIST_TIMEOUT_S = 5.0

#: The MCP actions, and which states each fits.
MCP_ACTIONS: tuple[str, ...] = ("reconnect", "off", "on", "sign_in")
_FITS: dict[str, frozenset[str]] = {
    "reconnect": frozenset({CONNECTED, FAILED}),
    "off": frozenset({CONNECTED, FAILED, NEEDS_SIGN_IN, STARTING}),
    "on": frozenset({OFF}),
    "sign_in": frozenset({NEEDS_SIGN_IN}),
}

KIND_WORDS = {"helper": "helper", "command": "background command", "task": "background task"}
NOT_RUNNING = "{label} is not running right now, so nothing runs in the background."
NO_BACKGROUND = "{label} has no background work Iron Jarvis can stop here."
NO_MCP = "{label} offers no way to do that to one of its MCP servers here."
NOT_NOW = "That does not fit {name} right now."
NO_ANSWER = "{label} did not answer in time."
STOPPED_WORDS = "You stopped the {kind} “{title}”."
STOPPED_REPLY_ON = " The reply goes on."
MCP_DONE_WORDS = {
    "reconnect": "Reconnected {name}.",
    "off": "Turned off {name}.",
    "on": "Turned on {name}.",
    "sign_in": "{label} waits for you to sign in to {name} in your browser.",
}
SIGN_IN_FROM_MENU = "Choose Sign in… in this row's menu: {label} opens its sign-in page in your browser. Or:"
#: Claude Code keeps a server it turned off off for this folder (measured).
OFF_REMEMBERED = {"claude-code": " Claude Code remembers this for this folder."}


def _label(harness: str) -> str:
    from .words import label

    return label(harness)


def actions_for(state: str, offered: Any) -> list[str]:
    """The actions one MCP row offers: the driver's, that fit its state."""
    have = set(offered or ())
    return [a for a in MCP_ACTIONS if a in have and state in _FITS[a]]


async def decorate_overview(view: dict[str, Any], live: Any) -> dict[str, Any]:
    """Add the live controls to ``GET /build/app/sessions/{id}/overview``:
    ``background_work`` (only when the running driver offers a list) and each
    MCP row's ``actions``. Never raises; a tool that does not answer in time
    leaves the view as it was."""
    driver = getattr(live, "driver", None) if live is not None and getattr(live, "running", False) else None
    if driver is None:
        return view
    offered = getattr(driver, "mcp_actions", None)
    mcp = view.get("mcp")
    if offered and isinstance(mcp, dict) and mcp.get("known"):
        for row in mcp.get("servers") or []:
            if isinstance(row, dict):
                acts = actions_for(str(row.get("state") or ""), offered)
                if acts:
                    row["actions"] = acts
                if "sign_in" in acts:
                    # The row's own menu is the easy way now; the tool's own
                    # terminal steps stay beside it.
                    first = SIGN_IN_FROM_MENU.format(label=_label(str(getattr(driver, "harness", ""))))
                    row["sign_in"] = f"{first} {row['sign_in']}" if row.get("sign_in") else first
    lister = getattr(driver, "background_work", None)
    if lister is not None:
        try:
            rows = await asyncio.wait_for(lister(), timeout=LIST_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — a slow or gone tool: no list this read
            rows = None
        if isinstance(rows, list):
            view["background_work"] = [
                {"id": clip(str(r.get("id") or ""), 80), "title": clip(str(r.get("title") or ""), 300),
                 "kind": str(r.get("kind") or "task") if r.get("kind") in KIND_WORDS else "task",
                 "can_stop": bool(r.get("can_stop"))}
                for r in rows if isinstance(r, dict) and r.get("id")
            ]
    return view


def _running_driver(service: Any, row: Any) -> tuple[Any, Any]:
    from .service import Refused

    live = service.live.get(row.id)
    driver = getattr(live, "driver", None) if live is not None and live.running else None
    if driver is None:
        raise Refused(NOT_RUNNING.format(label=_label(row.harness)), 409)
    return live, driver


async def stop_background(service: Any, sid: str, work_id: str) -> dict[str, Any]:
    """``POST /build/app/sessions/{id}/background/{work_id}/stop``: the tool's
    own stop for ONE background task; said in the conversation."""
    from .service import Refused

    row = await service._row(sid)
    label = _label(row.harness)
    live, driver = _running_driver(service, row)
    stopper = getattr(driver, "stop_background", None)
    if stopper is None:
        raise Refused(NO_BACKGROUND.format(label=label), 400)
    before = await driver.background_work() if getattr(driver, "background_work", None) else None
    kind = next((r.get("kind") for r in before or [] if r.get("id") == work_id), "task")
    try:
        title = await asyncio.wait_for(stopper(str(work_id)), timeout=ACTION_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise Refused(NO_ANSWER.format(label=label), 409) from None
    except ControlRefused as exc:
        raise Refused(str(exc), 409) from None
    running_turn = live.turn is not None and live.turn.running
    text = STOPPED_WORDS.format(kind=KIND_WORDS.get(str(kind), "background task"), title=clip(title, 200))
    service._emit(live, make("notice", text=text + (STOPPED_REPLY_ON if running_turn else "")))
    after = await decorate_overview({}, live)
    return {"stopped": str(work_id), "title": title, "background_work": after.get("background_work", [])}


async def mcp_action(service: Any, sid: str, server: str, action: str) -> dict[str, Any]:
    """``POST /build/app/sessions/{id}/mcp/{server}`` ``{action}``: one of the
    tool's own MCP actions (only those its driver offers, and only one that
    fits the server's state as last reported)."""
    from . import tool_setup as TS
    from .service import Refused

    row = await service._row(sid)
    label = _label(row.harness)
    if action not in MCP_ACTIONS:
        raise Refused(NO_MCP.format(label=label), 400)
    live, driver = _running_driver(service, row)
    doer = getattr(driver, "mcp_action", None)
    if doer is None or action not in set(getattr(driver, "mcp_actions", ()) or ()):
        raise Refused(NO_MCP.format(label=label), 400)
    known = {str(s.get("name")): str(s.get("state") or "") for s in (driver.mcp_servers or [])}
    name = clip(str(server or ""), TS.NAME_CHARS)
    if name not in known:
        raise Refused(f"{label} has no MCP server named {name} in this session.", 404)
    if action not in actions_for(known[name], {action}):
        raise Refused(NOT_NOW.format(name=name), 409)
    try:
        out = await asyncio.wait_for(doer(name, action), timeout=ACTION_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise Refused(NO_ANSWER.format(label=label), 409) from None
    except ControlRefused as exc:
        raise Refused(str(exc), 409) from None
    text = MCP_DONE_WORDS[action].format(name=name, label=label)
    if action == "off":
        text += OFF_REMEMBERED.get(row.harness, "")
    service._emit(live, make("notice", text=text))
    result: dict[str, Any] = {"server": name, "action": action, "words": text}
    if isinstance(out, dict) and out.get("url"):
        result["url"] = str(out["url"])
    return result
