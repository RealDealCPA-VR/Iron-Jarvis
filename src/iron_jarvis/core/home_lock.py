"""One daemon per data folder (v1.335.0, borrowed from pi-durable's "one
process owns a storage at a time").

Before this the only guard against a second daemon was the PORT probe in
``serve``: two daemons on different ports (a source run beside the installed
app, a second desktop copy started with another port) could open the same
home and write the same database, the same terminal snapshot and the same
scheduler rows at once. ``acquire`` takes an OS lock on ``<home>/daemon.lock``
for the life of the process — the OS drops it when the process ends, crash
included, so there is no stale lock to clean up — and records who holds it in
``<home>/daemon-owner.json`` so the refusal can name the other copy.

Only ``serve`` takes the lock. The CLI's one-shot commands (doctor, tools,
sessions…) read a home a daemon is running in on purpose and must keep
working.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

LOCK_NAME = "daemon.lock"
OWNER_NAME = "daemon-owner.json"

#: How long ``acquire`` keeps trying. The desktop shell respawns a crashed
#: daemon at once, and the dying process can still hold the lock for a moment.
DEFAULT_WAIT_S = 10.0
_POLL_S = 0.25

#: The open lock file of THIS process (held until exit — never closed).
_HELD: Any = None


class HomeLockHeld(RuntimeError):
    """Another live process owns this home."""

    def __init__(self, home: Path, owner: dict[str, Any] | None) -> None:
        self.home = home
        self.owner = owner or {}
        who = []
        if self.owner.get("pid"):
            who.append(f"pid {self.owner['pid']}")
        if self.owner.get("port"):
            who.append(f"port {self.owner['port']}")
        named = f" ({', '.join(who)})" if who else ""
        # "database is locked" on purpose: the desktop's startup classifier
        # keys on it and tells the user a second copy is the usual cause.
        # ASCII only: this line reaches a console whose code page may not
        # have a dash.
        super().__init__(
            f"Another Iron Jarvis{named} is already using {home}; the database is"
            " locked by another program, so this copy will not start."
        )


def _try_lock(fh: Any) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - exercised on POSIX only
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def read_owner(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / OWNER_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def acquire(
    home: str | Path, *, port: int | None = None, wait_s: float | None = None
) -> Any:
    """Take the home's lock for the rest of this process, or raise
    :class:`HomeLockHeld`. Returns the open lock file (keep it open)."""
    global _HELD
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    fh = open(home / LOCK_NAME, "a+b")  # noqa: SIM115 — held for the process life
    if fh.seek(0, os.SEEK_END) == 0:
        fh.write(b"\0")  # a byte to lock (Windows locks a range)
        fh.flush()
    wait = DEFAULT_WAIT_S if wait_s is None else wait_s
    deadline = time.monotonic() + max(0.0, wait)
    while not _try_lock(fh):
        if time.monotonic() >= deadline:
            fh.close()
            raise HomeLockHeld(home, read_owner(home))
        time.sleep(_POLL_S)
    try:
        (home / OWNER_NAME).write_text(
            json.dumps({"pid": os.getpid(), "port": port, "started": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass  # the name is a courtesy; the lock is what counts
    _HELD = fh
    return fh
