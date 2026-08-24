"""Detect installed AI coding CLIs so a terminal pane can launch them.

The dashboard shows a "Launch" dropdown of the CLIs actually present on this
machine (Claude Code, Codex, Grok, opencode, …). Picking one types its launch
command into the shell — the user presses Enter to start it. Detection is a
PATH lookup (``shutil.which``) augmented with a few common per-user bin dirs
that GUI-launched processes sometimes miss.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

#: Control characters that must never reach a live ConPTY pane. A newline or
#: carriage return typed into a running CLI SUBMITS the prompt, so a path
#: carrying one would send half a message; the rest of C0 (plus DEL) either
#: moves the cursor or is swallowed by the TUI's line editor.
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {"\x7f"}

#: Known AI CLIs: ``command`` is the exact text typed into the shell (a trailing
#: space means "expects an argument"). Order = display order.
AI_CLIS: list[dict[str, str]] = [
    {"id": "claude", "label": "Claude Code", "command": "claude", "provider": "Anthropic", "url": "https://claude.com/claude-code"},
    {"id": "codex", "label": "Codex", "command": "codex", "provider": "OpenAI", "url": "https://developers.openai.com/codex/cli"},
    {"id": "grok", "label": "Grok CLI", "command": "grok", "provider": "xAI", "url": "https://github.com/superagent-ai/grok-cli"},
    {"id": "opencode", "label": "opencode", "command": "opencode", "provider": "opencode", "url": "https://opencode.ai"},
    {"id": "pi", "label": "Pi", "command": "pi", "provider": "Earendil", "url": "https://github.com/earendil-works/pi"},
    {"id": "gemini", "label": "Gemini CLI", "command": "gemini", "provider": "Google", "url": "https://github.com/google-gemini/gemini-cli"},
    {"id": "cursor-agent", "label": "Cursor Agent", "command": "cursor-agent", "provider": "Cursor", "url": "https://cursor.com/cli"},
    {"id": "aider", "label": "Aider", "command": "aider", "provider": "Aider", "url": "https://aider.chat"},
    {"id": "crush", "label": "Crush", "command": "crush", "provider": "Charm", "url": "https://github.com/charmbracelet/crush"},
    {"id": "goose", "label": "Goose", "command": "goose", "provider": "Block", "url": "https://block.github.io/goose"},
    {"id": "qwen", "label": "Qwen Code", "command": "qwen", "provider": "Alibaba", "url": "https://github.com/QwenLM/qwen-code"},
    {"id": "llm", "label": "llm", "command": "llm ", "provider": "Datasette", "url": "https://llm.datasette.io"},
    {"id": "ollama", "label": "Ollama", "command": "ollama run ", "provider": "Ollama", "url": "https://ollama.com"},
]


#: Per-CLI "run without permission prompts" LAUNCH FLAG for Creative Studio
#: autopilot. THE CANONICAL HOME (v1.175.0): the Studio's checkbox used to
#: DESCRIBE this in hand-written prose, and the prose went stale twice over —
#: it still promised Claude engaged auto-accept "via Shift+Tab after boot" (that
#: mechanism was replaced by the flag because Shift+Tab could never reach a
#: genuinely hands-off mode) and that Codex launched with ``--full-auto`` (codex
#: ≥0.4x REMOVED that flag; launching with it exited instantly). A user reading
#: the checkbox was told something milder than what actually ran.
#:
#: So the flag is DATA now, served on every CLI record by :func:`detect_ai_clis`
#: and rendered verbatim by the dashboard. There is one string, in one place,
#: and the UI cannot describe a flag other than the one that is passed.
#: ``routes/creative.py`` re-exports this as ``_AUTOPILOT_FLAGS``.
AUTOPILOT_FLAGS: dict[str, str] = {
    "codex": "--dangerously-bypass-approvals-and-sandbox",
    "claude": "--dangerously-skip-permissions",
}


def image_reference(cli: str, path: str | os.PathLike[str]) -> str:
    """The exact text to type into a RUNNING ``cli`` pane so it reads ``path``.

    The launch-time counterpart of :data:`AUTOPILOT_FLAGS`: that says how to
    START a CLI, this says how to hand one an image once it is already running.

    A ConPTY pane is a byte stream — there is no image channel to paste into.
    Every supported CLI, however, reads images OFF DISK when given a path, and
    the daemon runs on the same machine as the CLI child process, so a path is
    genuinely shared. So the answer is always a path, quoted.

    Evidence, per CLI:

    * **Claude Code** reads an image from a path referenced in the prompt, and
      that is the ONLY method documented to work on every platform. Its
      clipboard paste on Windows is **Alt+V**, and plain Ctrl+V after
      ``Win+Shift+S`` is documented to do NOTHING on Windows native — which is
      exactly this user's platform. A bare quoted path is the safe answer.
    * **Codex CLI** accepts ``--image``/``-i`` with comma-separated paths at
      LAUNCH, and also reads a path referenced in the prompt. Inside an
      already-running interactive TUI you cannot add a launch flag, so the
      in-prompt path is the correct delivery for a live pane. **Do not "fix"
      this to ``-i``** — that string typed into a running Codex session is just
      prompt text with a stray flag in it.
    * **Every other CLI, and any UNKNOWN one**, degrades to the same plain
      quoted path. That degrade is deliberate, not a stub: a path in the prompt
      is the one delivery no CLI can misparse into an action.

    Why no ``@file`` mention or ``/add`` slash command for the CLIs that have
    them: both open an INTERACTIVE completion popup in a TUI, so the characters
    that follow land in a fuzzy-finder rather than in the prompt, and what ends
    up submitted depends on the CLI's version and the pane's width. This
    function's whole reason to exist is to avoid CLI- and platform-specific
    keystroke tricks.

    NEVER RAISES and never returns a bare unquoted fragment:

    * spaces are covered by the quotes (a Windows screenshot path lives under
      ``AppData\\Local\\Temp``-shaped dirs and routinely contains them);
    * a path containing ``"`` is single-quoted instead, and one containing both
      quote characters keeps double quotes with the inner ones backslashed;
    * control characters are STRIPPED — a newline in a path would submit the
      prompt mid-sentence (see :data:`_CONTROL_CHARS`);
    * an empty/whitespace-only path returns ``""`` (the empty string, not an
      empty pair of quotes), because there is nothing to reference and typing
      ``""`` into a pane is noise the caller cannot detect.

    ``cli`` is taken but deliberately does not change the result today — every
    CLI above lands on the same in-prompt path. It is a parameter rather than an
    assumption so that a CLI which later needs a different delivery changes HERE
    and at no call site; it is also never inspected, which is half of why this
    function cannot raise on an unknown, empty, or oddly-cased key.
    """
    if path is None:
        return ""
    if isinstance(path, str):
        raw: str = path
    else:
        try:
            raw = os.fspath(path)
        except Exception:  # a caller handed us something odd — degrade, don't raise
            raw = str(path)
    text = "".join(ch for ch in str(raw) if ch not in _CONTROL_CHARS).strip()
    if not text:
        return ""
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    return '"' + text.replace('"', '\\"') + '"'


def _extra_bin_dirs() -> list[Path]:
    """Common per-user tool bin dirs that a GUI-launched daemon's PATH may miss
    (npm/pipx/cargo/bun/deno global installs)."""
    home = Path.home()
    dirs = [
        home / ".local" / "bin",
        home / "bin",
        home / ".cargo" / "bin",
        home / ".bun" / "bin",
        home / ".deno" / "bin",
        # Tool-home bin dirs: some CLIs install into their own dot-dir rather
        # than a shared PATH location. The Grok CLI (~/.grok/bin/grok.exe) is
        # the driving case — without this it detects as "Not installed".
        home / ".grok" / "bin",
        home / ".xai" / "bin",
    ]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "npm")  # npm global shims (*.cmd)
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Programs")
            # Pi's installer ships a bundled Node runtime and drops pi.cmd in
            # %LOCALAPPDATA%\pi-node\current, prepending it to the USER PATH —
            # which a GUI-launched daemon whose environment predates the
            # install never sees (same driving case as ~/.grok/bin above).
            dirs.append(Path(local) / "pi-node" / "current")
    return [d for d in dirs if d.is_dir()]


def _find(command: str) -> str | None:
    """Resolve ``command`` to an executable path, or None. Tries the real PATH
    first, then a few well-known per-user bin dirs (with Windows extensions)."""
    exe = command.strip().split()[0] if command.strip() else command
    found = shutil.which(exe)
    if found:
        return found
    exts = ["", ".cmd", ".exe", ".bat", ".ps1"] if os.name == "nt" else [""]
    for d in _extra_bin_dirs():
        for ext in exts:
            cand = d / (exe + ext)
            if cand.is_file():
                return str(cand)
    return None


def detect_ai_clis() -> list[dict[str, Any]]:
    """The full catalog, each tagged ``installed`` (+ resolved ``path``) and
    carrying its ``autopilot_flag`` ("" when that CLI has none), so the Studio's
    UI names the EXACT flag it will launch with instead of a prose copy that
    drifts (v1.175.0)."""
    out: list[dict[str, Any]] = []
    for cli in AI_CLIS:
        path = _find(cli["command"])
        out.append(
            {
                **cli,
                "installed": path is not None,
                "path": path,
                "autopilot_flag": AUTOPILOT_FLAGS.get(cli["id"], ""),
            }
        )
    return out
