"""A FOLDER carries its own defaults for coding sessions started in it
(v1.426.0, Wave 6 lane B, E5 of ``docs/HARNESS-BUILD-AUDIT-2026-11-01.md``).

Every new coding session used to start on the app's default model, its
default reasoning level and "Ask first" — in every folder, however the user
always worked there. Now a folder can say what its NEW sessions start with,
for each of the two coding views:

* ``code`` — Chat › Code (Iron Jarvis's own coding sessions): ``provider`` +
  ``model`` (a row of the model menu), ``reasoning`` (a level of
  ``providers/reasoning``) and ``approval_mode`` (the permission chip).
* ``tool`` — Build › Code (the user's real Claude Code / Codex / …):
  ``harness`` (the tool), ``model`` and ``effort`` (the tool's own words) and
  ``mode`` (the tool's own permission mode).

STORED on ``BuildFolderRecord.defaults_json`` (additive; the reconciler adds
the column to an older database, and a row an older app wrote reads as no
defaults), keyed like the folder's display name and its team
(``harness/sessions.folder_key``: the folder the user PICKED — a branch
session's worktree never has its own). The display name and the team are
untouched by a save here.

USED by the two start composers only (``HarnessStartComposer`` /
``StartComposer``): when a session starts in the folder, a chip the user did
not touch shows the folder's value and the create carries it; a chip the
user picked wins; the words under the chips say which defaults are in use.
The create routes are NOT changed (an API caller gets exactly what it sends).

NEVER A FOLDER DEFAULT: "Don't ask" (Chat › Code's ``yolo``) and a Build ›
Code tool's Full access — each is chosen per session, on purpose, with its
own words or two presses; a folder that started every session that way would
take that choice away. Refused in words (400).

Every read and write is BLOCKING (SQLite) — the routes run them in
FastAPI's thread pool — and a read never raises (a bad row reads as none).
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The two coding views a folder keeps defaults for.
VIEW_CODE = "code"
VIEW_TOOL = "tool"
VIEWS: tuple[str, ...] = (VIEW_CODE, VIEW_TOOL)

#: Chat › Code's postures a folder may start a session with (``yolo`` — "Don't
#: ask" — never; see the module doc).
CODE_POSTURES: tuple[str, ...] = ("always_ask", "approve_for_me", "accept_edits", "auto")
#: The Build › Code tools (``hosted/words.LABELS``).
TOOLS: tuple[str, ...] = ("claude-code", "codex", "pi", "opencode", "grok", "antigravity")
#: A Build › Code tool's modes a folder may start a session with (Full access
#: never; each tool still offers only its own — the composer checks).
TOOL_MODES: tuple[str, ...] = ("ask", "edits", "plan", "auto", "own")

NO_DONT_ASK = (
    "\"Don't ask\" is chosen per session, never as a folder's default — pick another "
    "approval level for new sessions here."
)
NO_FULL_ACCESS = (
    "Full access is chosen per session (two presses), never as a folder's default — pick "
    "another mode for new sessions here."
)
_WORD = re.compile(r"^[A-Za-z0-9_.:\[\]/@+-]{1,200}$")


class DefaultsRefused(ValueError):
    """A default the folder cannot keep (said in words; the route's 400)."""

    status = 400


def _ident(raw: Any, what: str, cap: int = 200) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if len(text) > cap or not _WORD.match(text):
        raise DefaultsRefused(f"That {what} is not one Iron Jarvis knows.")
    return text


def clean_code(raw: Any) -> dict[str, str] | None:
    """Chat › Code's defaults, checked; None = nothing set (= clear)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DefaultsRefused("Say the folder's defaults as model, reasoning and approval level.")
    from ..providers.reasoning import ALL_LEVELS

    provider = _ident(raw.get("provider"), "model")
    model = _ident(raw.get("model"), "model")
    if model and not provider:
        raise DefaultsRefused("A model needs the provider it runs on.")
    reasoning = str(raw.get("reasoning") or "").strip().lower()
    if reasoning and reasoning not in ALL_LEVELS:
        raise DefaultsRefused("That reasoning level is not one Iron Jarvis knows.")
    posture = str(raw.get("approval_mode") or "").strip()
    if posture == "yolo":
        raise DefaultsRefused(NO_DONT_ASK)
    if posture and posture not in CODE_POSTURES:
        raise DefaultsRefused("That approval level is not one Iron Jarvis knows.")
    out = {k: v for k, v in (("provider", provider), ("model", model), ("reasoning", reasoning),
                             ("approval_mode", posture)) if v}
    return out or None


def clean_tool(raw: Any) -> dict[str, str] | None:
    """Build › Code's defaults, checked; None = nothing set (= clear)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DefaultsRefused("Say the folder's defaults as tool, model, effort and mode.")
    harness = str(raw.get("harness") or "").strip()
    if not harness:
        raise DefaultsRefused("Say which coding tool new sessions here start with.")
    if harness not in TOOLS:
        raise DefaultsRefused("That coding tool is not one Iron Jarvis knows.")
    model = _ident(raw.get("model"), "model")
    effort = _ident(raw.get("effort"), "effort", 40)
    mode = str(raw.get("mode") or "").strip()
    if mode == "full":
        raise DefaultsRefused(NO_FULL_ACCESS)
    if mode and mode not in TOOL_MODES and not _opencode_agent(harness, mode):
        raise DefaultsRefused("That mode is not one Iron Jarvis knows.")
    return {k: v for k, v in (("harness", harness), ("model", model), ("effort", effort), ("mode", mode)) if v}


def _opencode_agent(harness: str, mode: str) -> bool:
    """v1.441.0 (review): one of the user's own OpenCode agents
    (``agent:<name>``). The Code view offers it as a mode and its start box
    offers to keep the chosen mode as the folder's default — refusing it said
    "not one Iron Jarvis knows". The composer uses a kept mode only while the
    tool still lists it, as for every other mode."""
    if harness != "opencode":
        return False
    from ..hosted.opencode import agent_of

    return bool(agent_of(mode))


CLEANERS = {VIEW_CODE: clean_code, VIEW_TOOL: clean_tool}


def _parse(raw: str) -> dict[str, dict[str, str]]:
    """A stored row's defaults, re-checked (a hand-edited or older value that
    no longer passes is left out — never raised)."""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for view, clean in CLEANERS.items():
        try:
            got = clean(data.get(view)) if isinstance(data, dict) else None
        except DefaultsRefused:
            got = None
        if got:
            out[view] = got
    return out


def read(engine: Any, folder: str) -> dict[str, dict[str, str]]:
    """``{code?: {...}, tool?: {...}}`` for ``folder`` ({} = none). BLOCKING;
    never raises."""
    from .sessions import folder_key

    key = folder_key(folder)
    if not key or engine is None:
        return {}
    try:
        from ..core.db import session_scope
        from ..core.models import BuildFolderRecord

        with session_scope(engine) as db:
            row = db.get(BuildFolderRecord, key)
            raw = str(getattr(row, "defaults_json", "") or "") if row is not None else ""
        return _parse(raw)
    except Exception:  # noqa: BLE001 — a bad row reads as none
        return {}


def save(engine: Any, folder: str, view: str, value: Any) -> dict[str, dict[str, str]]:
    """Save (or with None, clear) ``folder``'s defaults for ``view``; the
    other view's, the display name and the team are kept. Returns every
    default the folder now has. BLOCKING; raises :class:`DefaultsRefused`."""
    from ..core.db import CONVERSATION_WRITE_LOCK, session_scope
    from ..core.ids import utcnow
    from ..core.models import BuildFolderRecord
    from .sessions import folder_key

    if view not in CLEANERS:
        raise DefaultsRefused("Say which coding view the defaults are for.")
    key = folder_key(folder)
    if not key:
        raise DefaultsRefused("Say which folder the defaults are for.")
    clean = CLEANERS[view](value)
    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        row = db.get(BuildFolderRecord, key)
        current = _parse(str(getattr(row, "defaults_json", "") or "")) if row is not None else {}
        if clean:
            current[view] = clean
        else:
            current.pop(view, None)
        if row is None:
            if not current:
                return {}
            row = BuildFolderRecord(key=key, path=str(folder), name="")
        row.defaults_json = json.dumps(current, sort_keys=True) if current else ""
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
    return current
