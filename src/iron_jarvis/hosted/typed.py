"""v1.439.0: Claude Code's and Grok's own ``/`` commands typed in Build ›
Code's box — the Pi way (``hosted/pi.py`` ``typed_builtin`` /
``TERMINAL_ONLY``).

* ``/model <name>`` and ``/effort <level>`` set the session's existing chips
  through the service's ``patch`` (no turn of the tool's, one notice line),
  resolved against THAT tool's own model and effort lists, refused in words
  when unknown. Typed raw, Claude Code would switch inside its process while
  the chips (and the next restart) still said the old model; Grok has no
  ``/model`` over ACP at all (the words would go to the model).
* Claude Code: the built-ins that need its own terminal become note rows in
  the ``/`` menu and a typed one is answered in words — ONLY for names NOT in
  the command list Claude Code's own ``initialize`` returns. MEASURED
  2026-11-06 on claude 2.1.316 (``claude -p --input-format stream-json
  --output-format stream-json --verbose --strict-mcp-config`` in an empty
  scratch folder, only the ``initialize`` control request, no model call):
  its built-ins there are advisor, agents, auto-mode-setup, autocompact,
  batch, clear, color, compact, config, context, debug, doctor, effort, fast,
  focus, heapdump, init, mcp, import, model, output-style, reload-plugins,
  reload-skills, rename, ultrareview, security-review, usage-credits,
  extra-usage, usage, insights, recap, skill-doctor, goal, … — login, logout,
  permissions, memory, status, resume, rewind, export, hooks, help, theme and
  the others below are NOT, so Claude Code would hand them to the model as
  plain words. A name the running (or last) list has is always Claude
  Code's own: it is sent, never answered here (a later Claude Code may run
  it headless).
* Grok: ``/always-approve`` and ``/auto`` (Grok advertises the first over ACP,
  ``tests/fixtures/build_app_v1416/g_read.json``) would change what Grok runs
  without asking, outside Iron Jarvis's cards while the mode chip still said
  Ask first. They are dropped from the menu and refused typed, before
  anything is sent.
"""

from __future__ import annotations

from typing import Any

CLAUDE = "claude-code"
GROK = "grok"

#: The typed built-ins that set a chip (both tools).
SETTINGS: tuple[str, ...] = ("model", "effort")

_SCREEN = ("That is one of Claude Code's own screens, which needs its terminal — "
           "open this session there with Open in terminal.")
_MOVES = ("That moves Claude Code to another point of its conversation, which only its own terminal can "
          "follow — open this session there with Open in terminal.")
_SIGN_IN = ("Claude Code signs in on its own screen — press Sign in beside Claude Code in Settings › "
            "Connections, or open this session with Open in terminal and type /login there.")
#: Claude Code built-ins that need its own terminal, and the one line said
#: instead (only while the name is NOT in Claude Code's own list).
CLAUDE_TERMINAL_ONLY: dict[str, str] = {
    "login": _SIGN_IN,
    "logout": _SIGN_IN,
    "permissions": ("Claude Code's permission rules are its own screen — choose how it asks with the mode chip "
                    "under the box, or open this session with Open in terminal."),
    "memory": _SCREEN,
    "status": _SCREEN,
    "hooks": _SCREEN,
    "ide": _SCREEN,
    "terminal-setup": _SCREEN,
    "vim": _SCREEN,
    "theme": _SCREEN,
    "statusline": _SCREEN,
    "privacy-settings": _SCREEN,
    "help": _SCREEN,
    "add-dir": _SCREEN,
    "bug": _SCREEN,
    "feedback": _SCREEN,
    "release-notes": _SCREEN,
    "upgrade": _SCREEN,
    "export": _SCREEN,
    "rewind": _MOVES,
    "resume": "Your Claude Code conversations are listed with your sessions — open one there.",
    "exit": "There is nothing to quit here: Iron Jarvis stops Claude Code after the session has been idle a while.",
    "quit": "There is nothing to quit here: Iron Jarvis stops Claude Code after the session has been idle a while.",
}
#: Grok's own commands that change what it runs without asking.
GROK_APPROVAL: tuple[str, ...] = ("always-approve", "auto")
GROK_APPROVAL_REFUSAL = ("That would change what Grok runs without asking, outside Iron Jarvis's cards — "
                         "choose how Grok asks with the mode chip under the box.")
#: The rows Grok's ``/`` menu gains (it advertises neither over ACP).
GROK_SETTING_ROWS: tuple[dict[str, str], ...] = (
    {"name": "model", "description": "Choose the model", "argument_hint": "<model>"},
    {"name": "effort", "description": "Set the effort level", "argument_hint": "<level>"},
)
#: A typed command is a message of its own, never a note.
NOT_A_NOTE = "{label}'s own commands go as a message of their own — send it once {label} has finished."
MODEL_NEEDS_NAME = "Choose the model from the model menu under the box, or type /model and its name."
EFFORT_NEEDS_LEVEL = "Choose the effort level from the effort menu under the box, or type /effort and a level."
NO_SUCH_MODEL = "{label} does not list a model called {name}. Choose one from the model menu under the box."
TAKES_NO_FILES = "{label}'s own commands take no files — send the files in a message of their own."
#: How "back to the tool's own default" is typed.
DEFAULT_WORDS: tuple[str, ...] = ("default", "auto")


def parse(text: Any) -> tuple[str, str] | None:
    """``(name, words after it)`` for a message that starts with ``/``, else
    None. Pi's own reading (``pi.typed_builtin``): the first word, any case."""
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return None
    word, _, rest = stripped[1:].partition(" ")
    if "\n" in word:
        word, _, more = word.partition("\n")
        rest = (more + " " + rest).strip()
    name = word.strip().lower()
    return (name, rest.strip()) if name else None


def _names(commands: list[dict[str, Any]] | None) -> set[str]:
    return {str(c.get("name") or "").lower().lstrip("/") for c in commands or []
            if isinstance(c, dict) and not c.get("note")}


def typed_builtin(harness: str, text: Any, commands: list[dict[str, Any]] | None) -> tuple[str, str] | None:
    """``(name, words)`` when the message is one of the commands done here
    (a setting, a terminal-only Claude Code built-in, a Grok approval
    command); None for anything else (it goes to the tool as before)."""
    if harness not in (CLAUDE, GROK):
        return None
    got = parse(text)
    if got is None:
        return None
    name, rest = got
    if name in SETTINGS:
        return name, rest
    if harness == GROK and name in GROK_APPROVAL:
        return name, rest
    if harness == CLAUDE and name in CLAUDE_TERMINAL_ONLY and name not in _names(commands):
        return name, rest
    return None


def refusal(harness: str, name: str) -> str:
    """The one line for a typed command done nowhere ("" for a setting)."""
    if harness == GROK and name in GROK_APPROVAL:
        return GROK_APPROVAL_REFUSAL
    if harness == CLAUDE and name in CLAUDE_TERMINAL_ONLY:
        return CLAUDE_TERMINAL_ONLY[name]
    return ""


def menu_rows(harness: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tool's own rows as the ``/`` menu shows them: Grok without its
    approval commands and with ``/model`` and ``/effort``; Claude Code with a
    note row for each terminal-only built-in its own list does not name."""
    out = [dict(r) for r in rows if isinstance(r, dict)]
    if harness == GROK:
        out = [r for r in out if str(r.get("name") or "").lower().lstrip("/") not in GROK_APPROVAL]
        have = _names(out)
        out = [dict(r) for r in GROK_SETTING_ROWS if r["name"] not in have] + out
    elif harness == CLAUDE and out:
        # Only once Claude Code has said its own list (never a start just to
        # ask): which built-ins it runs is read off that list.
        have = _names(out)
        out += [{"name": n, "description": "Only in Claude Code's own terminal", "note": why}
                for n, why in CLAUDE_TERMINAL_ONLY.items() if n not in have]
    return out


def resolve_model(arg: str, rows: list[dict[str, Any]]) -> str | None:
    """The model id ``/model <arg>`` means: "" (the tool's own default) for
    "default"; an exact id; else the ONE model whose id (or the part after a
    ``/``) or name is ``arg``, any case. None when none or several match."""
    want = str(arg or "").strip()
    if not want:
        return None
    if want.lower() in DEFAULT_WORDS:
        return ""
    ids = [str(r.get("id") or "") for r in rows if r.get("id")]
    if want in ids:
        return want
    low = want.lower()
    hits = {str(r["id"]) for r in rows if r.get("id") and (
        str(r["id"]).lower() == low or str(r["id"]).split("/", 1)[-1].lower() == low
        or str(r.get("label") or "").lower() == low)}
    return next(iter(hits)) if len(hits) == 1 else None
