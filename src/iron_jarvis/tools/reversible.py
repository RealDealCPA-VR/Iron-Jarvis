"""Whether an ask's action can be UNDONE, for the approval card (v1.352.0, G25).

The registry has always known each tool's undo contract (``Tool.reversibility``,
TX-01) but said it only in the ledger, after the fact: allowing a sent message,
a command or a browser click looked exactly like allowing a file save the undo
journal can put back. Every ask now carries the verdict, so the card and the
bell can say it in one quiet line before the user answers.

ONE definition, read by both places that announce an ask (MIRROR NOTE,
lock-step): the stream lane's ``approval`` frame (``daemon/routes/chat.py``)
and the agent runtime's ``approval.requested`` payload (``agents/runtime.py``),
which ``GET /chat/approvals/pending`` copies key-by-key for the bell.

Three answers, one key:

* ``True``  — ``REVERSIBLE``: the registry snapshots an inverse before the
  call (a file write, a settings or record change) — "You can undo this."
* ``False`` — ``IRREVERSIBLE``, which is also the fail-safe DEFAULT for a tool
  that never declared itself (every app/pack tool, every custom command):
  "This may not be undoable." ("may": undeclared is not proven.)
* ``None``  — the key is LEFT OUT: a ``READONLY`` tool changes nothing, so
  there is nothing to undo and the card says nothing; and no tool at all (a
  name the registry does not hold, such as the daemon's own plan approval).

WORDING ONLY and never a new failure: any fault reading the attribute answers
``None`` (no line), never a wrong promise.
"""

from __future__ import annotations

from typing import Any

from .base import Reversibility


def reversible_flag(tool: Any) -> bool | None:
    """``True`` / ``False`` / ``None`` for *tool* (see the module docstring)."""
    if tool is None:
        return None
    try:
        raw = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
        value = Reversibility(getattr(raw, "value", raw))
    except Exception:  # noqa: BLE001 — wording only, never a new failure
        return None
    if value is Reversibility.READONLY:
        return None
    return value is Reversibility.REVERSIBLE
