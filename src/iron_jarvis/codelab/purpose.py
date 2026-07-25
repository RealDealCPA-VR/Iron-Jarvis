"""What a saved script is FOR (v1.96.0).

The gallery shows one line per script: the USE CASE. Getting a real sentence
there matters more than it sounds — ``run_code`` names an unnamed script
``run_<epoch>``, so a tile without a purpose reads "run_1753459200" and the
gallery is unusable.

Two sources, in order:

1. The agent SAYS so — ``run_code`` takes a ``purpose`` argument and its
   description asks for one. Best case: a human sentence about intent.
2. Failing that, we READ it out of the code — a leading docstring or comment
   header. Costs nothing, and scripts written before ``purpose`` existed (or by
   an agent that skipped it) still get a usable line.

Never invented: when neither source yields anything we return "" and the tile
says so plainly rather than showing a fabricated summary of code we did not
understand.
"""

from __future__ import annotations

import re

#: Tiles are one line — long enough to be a sentence, short enough not to wrap
#: into a wall of text.
MAX_PURPOSE = 160

_SHEBANG = re.compile(r"^#!")
#: Lines that are decoration, not description.
_NOISE = re.compile(r"^[-=*#/\s]*$")


def _clean(line: str) -> str:
    line = line.strip().strip("#").strip()
    line = line.strip('"').strip("'").strip()
    # Drop a leading "Purpose:"/"Description:" label — the tile already says it.
    line = re.sub(r"^(purpose|description|summary|goal)\s*[:\-]\s*", "", line, flags=re.I)
    return line.strip()


def derive_purpose(source: str, language: str = "python") -> str:
    """Best-effort one-liner describing what ``source`` does, or "" when the
    code says nothing about itself.

    Reads only the HEADER of the file — a docstring or comment block before the
    first real statement. A comment further down describes a step, not the
    script, and would mislead on a tile.
    """
    text = (source or "").strip()
    if not text:
        return ""

    # A leading triple-quoted docstring (python) or <# #> block (powershell).
    m = re.match(r'^(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', text, re.S)
    if not m and language == "powershell":
        m = re.match(r"^<#(.*?)#>", text, re.S)
    if m:
        for raw in m.group(1).splitlines():
            line = _clean(raw)
            if line and not _NOISE.match(line):
                return line[:MAX_PURPOSE]

    # Otherwise the leading comment block, skipping a shebang and decoration.
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if _SHEBANG.match(stripped):
            continue
        if not stripped.startswith("#"):
            break  # real code began — no header comment
        line = _clean(stripped)
        if line and not _NOISE.match(line):
            return line[:MAX_PURPOSE]
    return ""


def purpose_for(source: str, language: str, stated: str = "") -> str:
    """The purpose to store: what the agent stated, else what the code says."""
    stated = (stated or "").strip()
    if stated:
        return stated[:MAX_PURPOSE]
    return derive_purpose(source, language)
