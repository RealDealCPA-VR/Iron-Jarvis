"""A delete made on a PAGE is recorded and can be undone, like one made in chat
(v1.504.0, north-star plan 4.8).

The same removal was recorded and undoable from chat (the record tools in
``settings.records``: a ``record_restore`` journal row holding the record's
snapshot) but unrecorded and permanent from its page: ``DELETE /schedules/…``,
``/workflows/…``, ``/agents/…``, ``/webhooks/…``, ``/sentinels/…``,
``/goals/…``, ``/reflex/rules/…``, ``/lessons/…``, ``/memory/preferences/…``,
``/tools/custom/…``. Each of those routes now:

1. snapshots the record BEFORE removing it, with the very snapshot the chat
   tool takes (:func:`snapshot`, ``records._SNAP``);
2. removes it the way it always did;
3. writes ONE ledger row (``ToolInvocation`` under the chat tool's own name, so
   ``GET /config/ledger`` lists it as "Changed here" and "undo that" finds it)
   and ONE ``record_restore`` journal row (:func:`journal_removal`), so
   ``POST /undo/{id}`` puts it back through ``records.restore``.

A credential is different: its VALUE is never written into the journal. A key
removed or replaced on the Secrets page is RECORDED (names only) as a change
that can't be undone (:func:`journal_secret_change`), and the page says so.

Blocking (SQLite): the routes are sync ``def``s, run in FastAPI's threadpool.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..core.ids import new_id

log = logging.getLogger("ironjarvis.settings")

#: The session id a page's ledger rows are written under (as the settings
#: writer's and the credential store's are).
PAGE_SESSION = "settings"

#: A page-made removal with no chat tool of its own still needs a ledger name
#: (``records.ledger_tools`` and ``GET /config/ledger`` list these too).
PAGE_ONLY_TOOLS = frozenset({"custom_tool_delete"})

#: The record kind a custom tool's snapshot is filed under (``records.restore``).
CUSTOM_TOOL = "custom_tool"


def snapshot(platform: Any, record: str, name: str) -> Any:
    """The record as it is now, with the chat tool's own snapshot. Raises
    whatever the snapshot raises (the caller then removes nothing)."""
    from . import records as R

    return R._SNAP[record](platform, name)  # noqa: SLF001 — the one snapshot table


def journal_removal(
    platform: Any,
    *,
    tool: str,
    record: str,
    name: str,
    prior: Any,
    summary: str,
    extra: "dict[str, Any] | None" = None,
) -> str:
    """Record a removal made on a page, with its Undo. Returns the ledger
    row's id ("" when it could not be written: the removal stands and the log
    says so, as the settings writer does)."""
    from ..core.db import session_scope
    from ..core.models import PermissionMode, ToolInvocation, UndoJournal
    from ..tools.base import Reversibility
    from .records import RECORD_RESTORE

    inv_id = new_id("tool")
    desc: dict[str, Any] = {"record": record, "name": name, "prior": prior, "change_id": new_id("rec")}
    if extra:
        desc.update(extra)
    try:
        with session_scope(platform.engine) as db:
            db.add(
                ToolInvocation(
                    id=inv_id,
                    session_id=PAGE_SESSION,
                    agent_run_id="",
                    tool=tool,
                    args_json=json.dumps({"name": name, "actor": "settings_page"}),
                    verdict=PermissionMode.ALLOW,
                    ok=True,
                    output=summary[:4000],
                    reversibility=Reversibility.REVERSIBLE.value,
                )
            )
            db.add(
                UndoJournal(
                    action_id=inv_id,
                    session_id=PAGE_SESSION,
                    agent_run_id="",
                    tool=tool,
                    kind=RECORD_RESTORE,
                    reversible=True,
                    pre_inline=json.dumps(desc, default=str),
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001 — the removal already happened; say so in the log
        log.warning("a %s was removed on its page but could not be recorded", record, exc_info=True)
        return ""
    return inv_id


def journal_secret_change(platform: Any, name: str, status: str) -> str:
    """Record a key ``"removed"`` or ``"replaced"`` on the Secrets page: names
    only, and recorded as a change that CAN'T be undone (the value is never
    kept in the journal). Returns the ledger row's id, or ""."""
    from ..core.db import session_scope
    from ..core.models import PermissionMode, ToolInvocation, UndoJournal
    from ..tools.base import Reversibility

    inv_id = new_id("tool")
    try:
        with session_scope(platform.engine) as db:
            db.add(
                ToolInvocation(
                    id=inv_id,
                    session_id=PAGE_SESSION,
                    agent_run_id="",
                    tool="set_secret",
                    args_json=json.dumps({"secret": name, "status": status, "actor": "settings_page"}),
                    verdict=PermissionMode.ALLOW,
                    ok=True,
                    output=f"{status} {name} (can't be undone)",
                    reversibility=Reversibility.IRREVERSIBLE.value,
                )
            )
            db.add(
                UndoJournal(
                    action_id=inv_id,
                    session_id=PAGE_SESSION,
                    agent_run_id="",
                    tool="set_secret",
                    kind="secret_restore",
                    reversible=False,
                    pre_inline=json.dumps({"secret": name, "status": status, "can_undo": False}),
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("a key was %s on the Secrets page but could not be recorded", status, exc_info=True)
        return ""
    return inv_id


# ----------------------------------------------------------- custom tools


def snap_custom_tool(platform: Any, name: str) -> "dict[str, Any] | None":
    rec = platform.tools_registry.get(name)
    if rec is None:
        return None

    def _load(s: str) -> Any:
        try:
            return json.loads(s or "[]")
        except (TypeError, ValueError):
            return []

    return {
        "name": rec.name,
        "description": rec.description,
        "parameters": _load(rec.params_json),
        "command": _load(rec.argv_json),
        "timeout_seconds": int(rec.timeout_seconds or 60),
        "created_by": rec.created_by or "",
    }


def restore_custom_tool(platform: Any, name: str, prior: "dict[str, Any] | None") -> str:
    """Put a removed custom tool back as it was (registered live again), or
    remove one a create made. Never writes over a tool of that name made since."""
    from .records import RecordError

    reg = platform.tools_registry
    if prior is None:
        reg.remove(name)
        platform.registry.unregister(name)
        return f"undo: removed the custom tool '{name}'"
    if reg.get(name) is not None:
        raise RecordError(
            f"A custom tool called “{name}” was made since this removal, so Undo would overwrite it; "
            "rename or remove that one first."
        )
    rec = reg.register(
        name,
        str(prior.get("description") or ""),
        list(prior.get("parameters") or []),
        [str(a) for a in (prior.get("command") or [])],
        int(prior.get("timeout_seconds") or 60),
        created_by=str(prior.get("created_by") or ""),
    )
    platform.registry.register(reg.build_tool(rec), custom=True)
    return f"undo: brought back the custom tool '{name}'"
