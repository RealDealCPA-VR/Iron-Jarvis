"""What Jarvis can reach, read off the rules that enforce it (v1.352.0, G27).

Settings › Permissions & ledger opens with one calm block that says, in plain
words, what chat and agents can read and where they save. Two of those facts
live ONLY in the daemon, so this one read-only route hands them over:

* ``reads`` — ``core/fs_policy`` is the one read gate (``fs_read_ok``). It
  refuses the folders that hold the app's keys and records (the protected
  roots registered at boot) on every install, and — only when an install sets
  ``IRONJARVIS_FS_ALLOWLIST`` — everything outside the folders listed there.
  No approval level cards a read. ``limited`` is decided the way
  ``fs_path_allowed`` decides it (a non-blank variable), so a variable whose
  every folder fails to resolve reads as limited to nothing, which is what the
  gate then enforces; ``folders`` are ``allowlist_roots()``.
  v1.356.0 (G81): the user's own "Folders Jarvis may read" setting is a
  second limit the same gate enforces. ``chosen`` = those folders as the
  user typed them (the block names them, then "and its own working
  folders"), ``install`` = whether the installation's own limit is on;
  ``limited`` is true for either.
* ``writes`` — ``chat_files_dir``: where a chat outside a project keeps its
  folder (``config.chat_files_dir``: the "Chat files folder" setting, else
  ``<Documents>\\Iron Jarvis``). "" when it cannot be worked out.

No path to a protected folder rides here; the block names them in words.
A sync handler on purpose: resolving the Documents folder and canonicalising
the allowlist touch the filesystem, and FastAPI runs a sync handler in its
threadpool, never on the event loop (v1.153.1). Never raises: a fact that
cannot be read is reported empty, never as a 500 on a Settings page.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI

ALLOWLIST_ENV = "IRONJARVIS_FS_ALLOWLIST"


def access_facts(config: Any) -> dict[str, Any]:
    """The facts the Settings block renders. Never raises."""
    from ...core import fs_policy

    install = bool(os.environ.get(ALLOWLIST_ENV, "").strip())
    try:
        chosen = fs_policy.setting_folders()
    except Exception:  # noqa: BLE001 — an unreadable setting is reported empty
        chosen = []
    limited = install or bool(chosen)
    folders: list[str] = []
    if limited:
        try:
            folders = [
                str(p)
                for p in fs_policy.allowlist_roots()
                # The "nothing satisfies both limits" marker is not a folder
                # anyone chose: an empty list says "reads no files".
                if p != fs_policy.NOTHING_READABLE
            ]
        except Exception:  # noqa: BLE001 — an unreadable list is reported empty
            folders = []
    try:
        chat_dir = str(config.chat_files_dir)
    except Exception:  # noqa: BLE001 — no Documents folder resolvable
        chat_dir = ""
    return {
        "reads": {"limited": limited, "folders": folders, "chosen": chosen, "install": install},
        "writes": {"chat_files_dir": chat_dir},
    }


def register(app: FastAPI, d) -> None:
    """Attach the route to *app*; ``d`` is the create_app deps object."""

    @app.get("/settings/access")
    def settings_access() -> dict[str, Any]:
        """What chat and agents can read and where they save, from the rules
        that enforce it: ``{reads: {limited, folders, chosen, install},
        writes: {chat_files_dir}}``. Read-only; never fails."""
        return access_facts(getattr(d.platform, "config", None))
