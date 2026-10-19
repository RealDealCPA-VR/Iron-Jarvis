"""What the "a test never touches the user's real config files" guards compare.

Two of the guarded files are REWRITTEN by the apps that own them while the
suite runs on the user's own PC: Claude Code rewrites ``~/.claude.json``
(history, caches, project state; v1.344.0) and a running Claude Desktop
rewrites the ``preferences`` part of ``claude_desktop_config.json`` (seen
v1.358.0: three guard errors, the file's servers unchanged, the app open).
For those two files only the parts a pack import could ever write are
compared: the ``mcpServers`` (and, for ``.claude.json``, each project's).
Every other guarded file is compared byte for byte. One helper so the guards
cannot drift apart again."""
from __future__ import annotations

import json
from pathlib import Path

#: Files whose owner app rewrites them; only their ``mcpServers`` are compared.
SERVERS_ONLY = (".claude.json", "claude_desktop_config.json")


def _bytes(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except OSError:
        return None


def real_view(p: Path) -> bytes | str | None:
    """The comparable form of a guarded real file (``None`` = absent)."""
    raw = _bytes(p)
    if raw is None or p.name not in SERVERS_ONLY:
        return raw
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return raw
    if not isinstance(doc, dict):
        return raw
    view: dict = {"mcpServers": doc.get("mcpServers")}
    if p.name == ".claude.json":
        projects = doc.get("projects") if isinstance(doc.get("projects"), dict) else {}
        view["projects"] = {k: (v.get("mcpServers") if isinstance(v, dict) else None) for k, v in projects.items()}
    return json.dumps(view, sort_keys=True)
