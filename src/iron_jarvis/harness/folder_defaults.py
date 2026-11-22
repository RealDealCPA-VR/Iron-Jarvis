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


#: 22-b (R3.4, D-22.1): Build › Code's answer to "Work on a new branch in
#: this folder?", asked once per folder in the start box. ``{"work":
#: "branch"}`` = new sessions here start on their own branch (in their own
#: copy of the folder); ``{"work": "folder"}`` = in the folder directly, as
#: always. No answer kept = not asked yet (the start box offers it once).
VIEW_BRANCH = "branch"
BRANCH_WORK = ("branch", "folder")


def clean_branch(raw: Any) -> dict[str, str] | None:
    """The folder's answer, checked; None = nothing kept (= asked again)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DefaultsRefused("Say whether new Build › Code sessions here start on a new branch.")
    work = str(raw.get("work") or "").strip()
    if not work and isinstance(raw.get("on"), bool):
        work = "branch" if raw["on"] else "folder"
    if work not in BRANCH_WORK:
        raise DefaultsRefused("Say whether new Build › Code sessions here start on a new branch.")
    return {"work": work}


CLEANERS = {VIEW_CODE: clean_code, VIEW_TOOL: clean_tool, VIEW_BRANCH: clean_branch}
VIEWS = tuple(CLEANERS)


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
    # (22-b: ``branch`` is Build › Code's answer about a new branch; it is
    # saved and cleared like a view, and the other two keep it.)
    key = folder_key(folder)
    if not key:
        raise DefaultsRefused("Say which folder the defaults are for.")
    clean = CLEANERS[view](value)
    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        row = db.get(BuildFolderRecord, key)
        raw = str(getattr(row, "defaults_json", "") or "") if row is not None else ""
        current = _parse(raw)
        if clean:
            current[view] = clean
        else:
            current.pop(view, None)
        # 22-a: the folder's check after each reply rides the same row and is
        # kept as it is (``save_checks`` is its one writer).
        checks = _checks_of(raw)
        stored: dict[str, Any] = dict(current)
        if checks:
            stored[CHECKS_KEY] = checks
        if row is None:
            if not stored:
                return {}
            row = BuildFolderRecord(key=key, path=str(folder), name="")
        row.defaults_json = json.dumps(stored, sort_keys=True) if stored else ""
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
    return current


# --------------------------------------------------------------------------- #
# 22-a (R1.9): the folder's check after each Build › Code reply
# --------------------------------------------------------------------------- #
#: Where it is kept in ``defaults_json`` (beside the two views, never one).
CHECKS_KEY = "checks"
#: The longest command kept (one line, as a ``plugins.toml`` check's).
CHECK_COMMAND_CHARS = 500
#: A check's time limit: the default and the most a folder may keep.
CHECK_TIMEOUT_S = 300
CHECK_TIMEOUT_MAX_S = 1800
#: Who chose: the card under a reply ("card"), a sentence in chat ("chat").
CHECK_SOURCES = ("card", "chat")


def clean_checks(raw: Any) -> "dict[str, Any] | None":
    """The folder's check, checked; None = nothing kept. ``{"off": True,
    "at": …}`` = the user said not to run tests after replies here;
    ``{"command", "confirmed_at", "timeout_s", "source"}`` = the user said
    yes to EXACTLY this command. Never raises: a value that does not pass
    reads as nothing kept."""
    if not isinstance(raw, dict):
        return None
    at = str(raw.get("at") or raw.get("confirmed_at") or "")[:40]
    if raw.get("off") is True:
        return {"off": True, "at": at}
    command = str(raw.get("command") or "")
    if (not command.strip() or len(command) > CHECK_COMMAND_CHARS or "\n" in command or "\r" in command
            or "\x00" in command or not at):
        return None
    try:
        timeout = int(raw.get("timeout_s") or CHECK_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = CHECK_TIMEOUT_S
    timeout = max(1, min(timeout, CHECK_TIMEOUT_MAX_S))
    source = str(raw.get("source") or "card")
    return {"command": command, "confirmed_at": at, "timeout_s": timeout,
            "source": source if source in CHECK_SOURCES else "card"}


def _checks_of(raw: str) -> "dict[str, Any] | None":
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return None
    return clean_checks(data.get(CHECKS_KEY)) if isinstance(data, dict) else None


def read_checks(engine: Any, folder: str) -> "dict[str, Any] | None":
    """The folder's kept check (:func:`clean_checks`), or None. BLOCKING;
    never raises."""
    from .sessions import folder_key

    key = folder_key(folder)
    if not key or engine is None:
        return None
    try:
        from ..core.db import session_scope
        from ..core.models import BuildFolderRecord

        with session_scope(engine) as db:
            row = db.get(BuildFolderRecord, key)
            raw = str(getattr(row, "defaults_json", "") or "") if row is not None else ""
        return _checks_of(raw)
    except Exception:  # noqa: BLE001 — a bad row reads as none
        return None


def save_checks(engine: Any, folder: str, value: Any) -> "dict[str, Any] | None":
    """Keep (or with None, forget) the folder's check; the two views'
    defaults, the display name and the team are kept. Returns what is now
    kept. BLOCKING; raises :class:`DefaultsRefused` for a value that does
    not pass."""
    from ..core.db import CONVERSATION_WRITE_LOCK, session_scope
    from ..core.ids import utcnow
    from ..core.models import BuildFolderRecord
    from .sessions import folder_key

    key = folder_key(folder)
    if not key:
        raise DefaultsRefused("Say which folder the check is for.")
    clean = clean_checks(value) if value is not None else None
    if value is not None and clean is None:
        raise DefaultsRefused(
            "That check is not one Iron Jarvis can keep: one command on one line, at most "
            f"{CHECK_COMMAND_CHARS} characters."
        )
    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        row = db.get(BuildFolderRecord, key)
        raw = str(getattr(row, "defaults_json", "") or "") if row is not None else ""
        stored: dict[str, Any] = dict(_parse(raw))
        if clean:
            stored[CHECKS_KEY] = clean
        if row is None:
            if not stored:
                return None
            row = BuildFolderRecord(key=key, path=str(folder), name="")
        row.defaults_json = json.dumps(stored, sort_keys=True) if stored else ""
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
    return clean
