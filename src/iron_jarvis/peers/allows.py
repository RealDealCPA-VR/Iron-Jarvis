"""What a paired computer may do here: one switch per thing (next leaps 41-a).

Each pairing keeps, on its own record (:class:`~iron_jarvis.peers.models.
PeerRecord`), one switch per thing the OTHER computer may do on this one. Every
switch starts OFF for a new pairing, and only a press on the card (Settings ›
Connections › Your other computers, ``PATCH /peers/{name}/allows``) turns one
on. Chat can show them and turn them OFF, never on (D-41.2).

One table, :data:`SWITCHES`, says each switch's column, its words and whether
what it allows exists in this version (``ready``). A switch whose thing comes
in a later version (asking back, 41-b; handing over a chat, 41-c; coding work,
12-c; the models, 42 and 43; the work record from another computer) can be
turned off but never on, so it can never be on before the thing it allows
exists. A later phase flips its ``ready``.

A pairing made BEFORE this version (D-41.1) could already send tasks: its
columns are empty in the database (the schema reconciler adds them as
nullable), and :meth:`peers.store.PeerStore.settle_older` turns "Send me tasks"
on for it, everything else off, and marks it so the card says so once
(:data:`KEPT_TASKS`).

Pure: no database, no vault, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Switch:
    #: The column on ``PeerRecord`` and the key on the wire.
    key: str
    #: The switch's words on the card (VOCABULARY.md).
    label: str
    #: The few words in the one line under a computer ("desk may: send tasks").
    short: str
    #: Does what it allows exist in this version? Only a ready switch can be
    #: turned on.
    ready: bool
    #: The record field of its folder list ("" = it has none).
    folders: str = ""


SEND_TASKS = "may_send_tasks"
READ_RECORD = "may_read_record"
ASK_BACK = "may_ask_back"
HAND_OVER = "may_hand_over"
CODE = "may_code"
USE_MODELS = "may_use_models"
MANAGE_MODELS = "may_manage_models"

#: The folder lists (absolute folders), on the wire and in the view.
RECORD_FOLDERS = "record_folders"
CODE_FOLDERS = "code_folders"

SWITCHES: tuple[Switch, ...] = (
    Switch(SEND_TASKS, "Send me tasks", "send tasks", True),
    Switch(READ_RECORD, "Look up my work record", "look up your work record", False, RECORD_FOLDERS),
    Switch(ASK_BACK, "Ask me questions about tasks I send it", "ask you questions", False),
    Switch(HAND_OVER, "Hand me a whole chat", "hand you a whole chat", False),
    Switch(CODE, "Start coding work here", "start coding work", False, CODE_FOLDERS),
    Switch(USE_MODELS, "Use my models", "use your models", False),
    Switch(MANAGE_MODELS, "See and manage my models", "see and manage your models", False),
)
BY_KEY: dict[str, Switch] = {s.key: s for s in SWITCHES}
KEYS: tuple[str, ...] = tuple(BY_KEY)
FOLDER_FIELDS: tuple[str, ...] = (RECORD_FOLDERS, CODE_FOLDERS)

#: D-41.3: what one hand-over (41-c) may carry. Said on the switch, kept here
#: so the hand-over reads the same numbers.
HAND_OVER_MAX_FILES = 20
HAND_OVER_MAX_BYTES = 100 * 1024 * 1024

#: How many folders one list may hold, and how long one folder may be.
FOLDERS_MAX = 20
FOLDER_CHARS = 400

#: ``PeerRecord.kept_note``: the card says ONCE that "Send me tasks" was kept
#: on for a computer paired before this version (D-41.1).
KEPT_TASKS = "tasks"

#: The words for that one note.
KEPT_TASKS_WORDS = (
    "Paired before this version, so “Send me tasks” stays on for it, as before. "
    "Everything else starts off."
)

#: The answer when a press asks to turn on a switch whose thing is not here yet.
def not_ready_words(key: str) -> str:
    s = BY_KEY.get(key)
    label = s.label if s else key
    return f"“{label}” comes with a later version of Iron Jarvis, so it cannot be turned on yet."


#: Said by a door to a paired computer whose switch for it is off. The
#: sender's chat shows it after "<name> turned the task away:", so "this
#: computer" there is the SENDER, and the place named is HERE.
def switch_off_words(key: str, this_name: str, caller: str) -> str:
    s = BY_KEY.get(key)
    label = s.label if s else "that"
    here = this_name or "that computer"
    return (
        f"“{label}” is off for this computer on {here}. To allow it, open Settings › Connections › "
        f"Your other computers on {here} and turn on “{label}” under {caller}."
    )


def tasks_off_words(this_name: str, caller: str) -> str:
    """The task door's refusal (:func:`switch_off_words` for "Send me tasks")."""
    return switch_off_words(SEND_TASKS, this_name, caller)


def _flag(value: Any, key: str) -> bool:
    """A stored switch. ``None`` is a pairing from before this version whose
    columns were never settled (``PeerStore.settle_older`` failed): read the
    way the settling would have written it (D-41.1)."""
    if value is None:
        return key == SEND_TASKS
    return bool(value)


def folders_of(raw: Any) -> list[str]:
    """A stored folder list: absolute folders only, junk read as none."""
    try:
        got = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(got, list):
        return []
    out: list[str] = []
    for item in got:
        if isinstance(item, str) and item.strip() and item not in out:
            out.append(item[:FOLDER_CHARS])
        if len(out) >= FOLDERS_MAX:
            break
    return out


def allows_of(peer: Any) -> dict[str, Any]:
    """Every switch of ``peer`` (a ``PeerRecord``) and its folder lists."""
    out: dict[str, Any] = {s.key: _flag(getattr(peer, s.key, None), s.key) for s in SWITCHES}
    out[RECORD_FOLDERS] = folders_of(getattr(peer, "record_folders_json", None))
    out[CODE_FOLDERS] = folders_of(getattr(peer, "code_folders_json", None))
    return out


def on_keys(allows: dict[str, Any]) -> list[str]:
    """The switches that are on, in the table's order (the wire's ``allows``)."""
    return [s.key for s in SWITCHES if allows.get(s.key) is True]


def may(peer: Any, key: str) -> bool:
    """Is switch ``key`` on for ``peer``? An unknown key is never on."""
    if key not in BY_KEY or peer is None:
        return False
    return _flag(getattr(peer, key, None), key)


def wire_allows(raw: Any) -> list[str]:
    """What the OTHER computer says it lets this one do, read defensively:
    known switch keys only, each once, in the table's order. Never a
    permission here: it is only shown."""
    if not isinstance(raw, (list, tuple)):
        return []
    given = {str(x) for x in raw[:40] if isinstance(x, str)}
    return [k for k in KEYS if k in given]


def they_allow_of(raw: Any) -> dict[str, Any]:
    """The stored ``they_allow_json``: ``{known, allows, older, at}``."""
    try:
        got = json.loads(raw) if isinstance(raw, str) and raw else {}
    except (TypeError, ValueError):
        got = {}
    if not isinstance(got, dict) or not got:
        return {"known": False, "allows": [], "older": False, "at": None}
    at = got.get("at")
    return {
        "known": True,
        "allows": wire_allows(got.get("allows")),
        "older": got.get("older") is True,
        "at": at if isinstance(at, str) else None,
    }


def shorts(keys: Iterable[str]) -> str:
    """"send tasks and use your models" for the switches named."""
    words = [BY_KEY[k].short for k in keys if k in BY_KEY]
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def may_line(name: str, allows: dict[str, Any]) -> str:
    """The one line under a computer: "desk may: send tasks"."""
    on = shorts(on_keys(allows))
    return f"{name} may: {on}." if on else f"{name} may do nothing here yet."


def they_line(name: str, they: dict[str, Any]) -> str:
    """What the OTHER computer lets this one do, from its last answer."""
    if not they.get("known"):
        return f"{name} has not said yet what it lets this computer do. Press Check."
    if they.get("older"):
        return f"{name} runs an older Iron Jarvis: it takes tasks from this computer."
    on = shorts(they.get("allows") or [])
    return f"{name} lets this computer: {on}." if on else f"{name} lets this computer do nothing yet."


def table() -> list[dict[str, Any]]:
    """The switches as the card draws them (in order)."""
    return [
        {"key": s.key, "label": s.label, "short": s.short, "ready": s.ready, "folders": s.folders}
        for s in SWITCHES
    ]
