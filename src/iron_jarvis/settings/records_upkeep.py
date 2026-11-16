"""Iron Jarvis's own upkeep from chat (v1.506.0, north-star plan 6.3).

``backup_now`` makes a backup of Iron Jarvis right now: THE code the Back up
now press on Settings › System › Maintenance runs (``POST /diagnostics/repair``
``backup_now`` → ``maintenance.run_auto_backup``), with its copy to the backup
copy folder and its health entry. It reports the archive's absolute path.

Allow tier: it adds an archive under the app's own backups folder. As every
backup does (the daily one and the press), it keeps the newest seven and
removes an older one past that, except while a set-aside database waits for an
answer (``maintenance.set_aside_hold``); the tool says so. RESTORING a backup is
never a tool (it replaces the whole database and settings): that is a door to
the Maintenance card (``daemon/page_doors`` step ``restore_backup``).

BLOCKING work (an archive of the database) runs off the loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

NAME = "backup_now"
#: How many backups are kept (``maintenance.run_auto_backup``'s default).
KEPT = 7


def _d(platform: Any) -> Any:
    w = getattr(platform, "config_writer", None)
    return getattr(w, "d", None)


def make_backup(platform: Any) -> tuple[Path, dict[str, Any]]:
    """Back up now, exactly as the Maintenance card's press does. BLOCKING."""
    from ..maintenance import mirror_loop_health, mirror_status, run_auto_backup

    cfg = platform.config
    path = run_auto_backup(cfg.home, engine=platform.engine, config=cfg)
    status = mirror_status(cfg.home, cfg)
    entry = mirror_loop_health(status)
    d = _d(platform)
    if entry is not None and d is not None and isinstance(getattr(d, "loop_health", None), dict):
        d.loop_health["backup_mirror"] = entry
    return Path(path).resolve(), status


class BackupNowTool(Tool):
    name = NAME
    description = (
        "Back up Iron Jarvis right now: its database, settings and saved keys go into a new backup "
        "archive on this PC (and into the backup copy folder, when one is set), the same as Back up now "
        "in Settings › System › Maintenance. The newest seven backups are kept. It reports where the "
        "backup was written. It does NOT restore a backup: restoring is done only by the user on that "
        "Maintenance card."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.IRREVERSIBLE
    risk_class = RiskClass.PAGE_ACTION

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            path, status = await asyncio.to_thread(make_backup, self.platform)
        except Exception:  # noqa: BLE001 — said in plain words, never a traceback
            return ToolResult(ok=False, error="The backup could not be made. Try Back up now in Settings › System › Maintenance.")
        lines = [f"Backed up Iron Jarvis to {path}."]
        last = status.get("last") if isinstance(status, dict) else None
        if isinstance(status, dict) and status.get("configured"):
            if status.get("missing"):
                lines.append(f"It was NOT copied to the backup copy folder {status.get('dir')}: that folder is not there.")
            elif isinstance(last, dict) and last.get("ok") is False:
                lines.append(f"It was NOT copied to the backup copy folder: {last.get('error') or 'the copy failed'}.")
            else:
                lines.append(f"It was also copied to {status.get('dir')}.")
        lines.append(f"The newest {KEPT} backups are kept.")
        return ToolResult(ok=True, output=" ".join(lines), data={"path": str(path)})


def upkeep_tools(platform: Any) -> list[Tool]:
    return [BackupNowTool(platform)]
