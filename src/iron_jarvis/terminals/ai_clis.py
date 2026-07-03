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

#: Known AI CLIs: ``command`` is the exact text typed into the shell (a trailing
#: space means "expects an argument"). Order = display order.
AI_CLIS: list[dict[str, str]] = [
    {"id": "claude", "label": "Claude Code", "command": "claude", "provider": "Anthropic", "url": "https://claude.com/claude-code"},
    {"id": "codex", "label": "Codex", "command": "codex", "provider": "OpenAI", "url": "https://developers.openai.com/codex/cli"},
    {"id": "grok", "label": "Grok CLI", "command": "grok", "provider": "xAI", "url": "https://github.com/superagent-ai/grok-cli"},
    {"id": "opencode", "label": "opencode", "command": "opencode", "provider": "opencode", "url": "https://opencode.ai"},
    {"id": "gemini", "label": "Gemini CLI", "command": "gemini", "provider": "Google", "url": "https://github.com/google-gemini/gemini-cli"},
    {"id": "cursor-agent", "label": "Cursor Agent", "command": "cursor-agent", "provider": "Cursor", "url": "https://cursor.com/cli"},
    {"id": "aider", "label": "Aider", "command": "aider", "provider": "Aider", "url": "https://aider.chat"},
    {"id": "crush", "label": "Crush", "command": "crush", "provider": "Charm", "url": "https://github.com/charmbracelet/crush"},
    {"id": "goose", "label": "Goose", "command": "goose", "provider": "Block", "url": "https://block.github.io/goose"},
    {"id": "qwen", "label": "Qwen Code", "command": "qwen", "provider": "Alibaba", "url": "https://github.com/QwenLM/qwen-code"},
    {"id": "llm", "label": "llm", "command": "llm ", "provider": "Datasette", "url": "https://llm.datasette.io"},
    {"id": "ollama", "label": "Ollama", "command": "ollama run ", "provider": "Ollama", "url": "https://ollama.com"},
]


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
    ]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "npm")  # npm global shims (*.cmd)
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Programs")
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
    """The full catalog, each tagged ``installed`` (+ resolved ``path``)."""
    out: list[dict[str, Any]] = []
    for cli in AI_CLIS:
        path = _find(cli["command"])
        out.append({**cli, "installed": path is not None, "path": path})
    return out
