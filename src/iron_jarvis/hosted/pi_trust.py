"""v1.431.0: a Pi folder's OWN resources, which Pi skips without asking here.

Pi (1.3.0, ``dist/core/trust-manager.js`` + ``project-trust.js`` +
``main.js``) loads a folder's own settings and resources only when the folder
is TRUSTED. Its terminal ASKS; RPC mode — the way Build › Code runs Pi — has
no screen to ask on, so with no decision Pi SKIPS them silently (docs
``security.md``: "Print, JSON, and RPC modes cannot show the built-in trust
prompt"). This module reads, read-only and bounded, whether the folder of a
Code-view Pi session is in that state, so the view can say so and offer one
press that loads them for that session only (``pi --approve``, which trusts
the folder for that one process; nothing is written to Pi's ``trust.json``).

What needs a decision (``hasTrustRequiringProjectResources``): any of
:data:`PROJECT_RESOURCES` under ``<folder>/.pi`` (the folder ONLY, never a
parent), or a ``.agents/skills`` folder in the folder or ANY parent — except
the user's own ``~/.agents/skills`` (``HOME``, else the home folder). A bare
``.pi`` folder needs nothing.

Pi's order: ``--approve`` / ``--no-approve`` -> a user-level or command-line
extension answering the ``project_trust`` event (Iron Jarvis CANNOT see that:
said in the words) -> the NEAREST entry (folder, then parents) in
``<agent dir>/trust.json`` -> ``defaultProjectTrust`` in the agent dir's
``settings.json`` ("always" | "never" | anything else = "ask") -> no screen =
skipped.

Pi keys ``trust.json`` by ``realpathSync(path.resolve(cwd))``: Node's
JavaScript realpath follows links and keeps the case it was given. This module
spells the folder the same way, walking links ONE HOP AT A TIME and never
touching a network share (the v1.379.0 rule: ``code_editor._walk_links``);
a folder that cannot be spelled safely is :data:`UNKNOWN`, and nothing is
offered for it.

Every function here is BLOCKING (small file reads): call it off the loop.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import words as W

#: The folder needs no decision (nothing of its own for Pi to load).
NONE = "none"
#: Pi loads them: a saved "trust" decision, or ``defaultProjectTrust: always``.
TRUSTED = "trusted"
#: Pi skips them because the user TOLD it to (a saved "do not trust", or
#: ``defaultProjectTrust: never``): the user's own decision — nothing offered.
DECLINED = "declined"
#: Pi would have asked in its terminal and skips them here: OFFERED.
UNDECIDED = "undecided"
#: Iron Jarvis cannot tell (a network folder, a link it will not follow, an
#: unreadable trust list): nothing offered.
UNKNOWN = "unknown"

#: ``TRUST_REQUIRING_PROJECT_CONFIG_RESOURCES`` (trust-manager.js), with the
#: words each is said in.
PROJECT_RESOURCES: tuple[tuple[str, str], ...] = (
    ("extensions", "extensions"),
    ("skills", "skills"),
    ("prompts", "prompt templates"),
    ("themes", "themes"),
    ("mcp.json", "MCP servers"),
    ("SYSTEM.md", "system prompt"),
    ("APPEND_SYSTEM.md", "system prompt"),
    ("settings.json", "settings"),
)

#: The largest ``trust.json`` / ``settings.json`` read (Pi's own are tiny).
MAX_FILE_BYTES = 1_000_000
#: How many parents the ``.agents/skills`` walk looks at (a drive is never
#: this deep; a loop of junctions could otherwise be).
MAX_PARENTS = 64
#: An answer is kept this long per (folder, agent dir, home) — the view is
#: read every few seconds while a reply runs.
CACHE_S = 10.0

_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def _clock() -> float:
    return time.monotonic()


def _agent_dir(env: dict[str, str] | None) -> Path:
    """Pi's agent folder for a process started with ``env`` (else this one's)."""
    source = env if env is not None else os.environ
    value = str(source.get("PI_CODING_AGENT_DIR") or "").strip()
    return Path(os.path.expanduser(value)) if value else Path.home() / ".pi" / "agent"


def _home(env: dict[str, str] | None) -> str:
    """``process.env.HOME || homedir()`` for that process (Windows' home
    folder is ``USERPROFILE``)."""
    source = env if env is not None else os.environ
    return str(source.get("HOME") or "").strip() or os.path.expanduser("~")


def pi_path(raw: str) -> str | None:
    """The folder as Pi spells it (``realpathSync(path.resolve(...))``), or
    None when it cannot be spelled without touching a share or following a
    link Iron Jarvis does not follow."""
    from ..harness import code_editor as CE  # noqa: PLC0415 — keeps imports light

    text = str(raw or "").strip()
    if not text or CE._network_or_device(text):
        return None
    try:
        return CE._walk_links(os.path.abspath(text))
    except CE.EditorRefusal:
        return None
    except (OSError, ValueError):
        return None


def _exists(path: str) -> bool:
    """Is there anything at ``path`` (a link counts; it is never followed)."""
    try:
        os.lstat(path)
        return True
    except (OSError, ValueError):
        return False


def found_resources(folder: str, *, home: str) -> list[str]:
    """The words for what ``folder`` (already Pi-spelled) holds that needs a
    decision, in a fixed order, each said once; [] = nothing."""
    out: list[str] = []
    config = os.path.join(folder, ".pi")
    for name, words in PROJECT_RESOURCES:
        if words not in out and _exists(os.path.join(config, name)):
            out.append(words)
    own = pi_path(home)
    own_skills = os.path.join(own, ".agents", "skills") if own else None
    current = folder
    for _ in range(MAX_PARENTS):
        skills = os.path.join(current, ".agents", "skills")
        if skills != own_skills and _exists(skills):
            if "skills" not in out:
                out.append("skills")
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return out


def _read_json(path: Path) -> Any:
    """A small JSON file (BOM allowed), or raises ValueError / OSError."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("too large")
    text = path.read_text(encoding="utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    return json.loads(text)


def saved_decision(folder: str, agent: Path) -> bool | None:
    """The nearest saved decision for ``folder`` in ``<agent>/trust.json``
    (folder first, then each parent; exact keys, like Pi), or None. Raises
    ValueError for a trust list Pi itself could not read."""
    path = agent / "trust.json"
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    current = folder
    for _ in range(MAX_PARENTS):
        value = data.get(current)
        if value is True or value is False:
            return value
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def default_trust(agent: Path) -> str:
    """``defaultProjectTrust`` from the agent folder's ``settings.json``:
    "always" | "never" | "ask" (anything else, or no file, is "ask")."""
    try:
        data = _read_json(agent / "settings.json")
    except (OSError, ValueError):
        return "ask"
    value = data.get("defaultProjectTrust") if isinstance(data, dict) else None
    return value if value in ("always", "never") else "ask"


def check(folder: str, *, env: dict[str, str] | None = None, fresh: bool = False) -> dict[str, Any]:
    """BLOCKING. What Pi does with ``folder``'s own resources when Build ›
    Code starts it: ``{"state": NONE | TRUSTED | DECLINED | UNDECIDED |
    UNKNOWN, "found": [words…]}``. Never raises."""
    agent = _agent_dir(env)
    home = _home(env)
    key = (str(folder or ""), str(agent), home)
    now = _clock()
    kept = _CACHE.get(key)
    if not fresh and kept is not None and now - kept[0] < CACHE_S:
        return dict(kept[1])
    try:
        answer = _check(str(folder or ""), agent, home)
    except Exception:  # noqa: BLE001 — a view never fails over this
        answer = {"state": UNKNOWN, "found": []}
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[key] = (now, answer)
    return dict(answer)


def _check(folder: str, agent: Path, home: str) -> dict[str, Any]:
    if not folder.strip():
        return {"state": NONE, "found": []}
    spelled = pi_path(folder)
    if spelled is None:
        return {"state": UNKNOWN, "found": []}
    found = found_resources(spelled, home=home)
    if not found:
        return {"state": NONE, "found": []}
    try:
        saved = saved_decision(spelled, agent)
    except (OSError, ValueError):
        return {"state": UNKNOWN, "found": found}
    if saved is True:
        return {"state": TRUSTED, "found": found}
    if saved is False:
        return {"state": DECLINED, "found": found}
    default = default_trust(agent)
    if default == "always":
        return {"state": TRUSTED, "found": found}
    if default == "never":
        return {"state": DECLINED, "found": found}
    return {"state": UNDECIDED, "found": found}


def forget() -> None:
    """Drop every kept answer (tests; a press reads afresh anyway)."""
    _CACHE.clear()


def found_words(found: list[str]) -> str:
    """"extensions, skills and settings" — the list in plain words."""
    items = [str(x) for x in found if str(x).strip()]
    if not items:
        return W.PI_FOLDER_THINGS
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def view(folder: str, loaded: bool, *, env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """BLOCKING. The session view's ``pi_folder`` key, or None (say nothing):
    shown for a folder Pi would have ASKED about, or one this session already
    loads. ``offer`` is true only for :data:`UNDECIDED` not yet loaded."""
    got = check(folder, env=env)
    state, found = got["state"], list(got["found"])
    if not loaded and state != UNDECIDED:
        return None
    if loaded and state == NONE:
        found = []
    things = found_words(found)
    if loaded:
        line = W.PI_FOLDER_LOADED.format(things=things)
    else:
        line = W.PI_FOLDER_SKIPPED.format(things=things)
    return {
        "state": state,
        "found": found,
        "loaded": bool(loaded),
        "offer": state == UNDECIDED and not loaded,
        "line": line,
        "title": W.PI_FOLDER_TITLE,
    }
