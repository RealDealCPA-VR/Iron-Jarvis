"""A project folder's own instruction files, read for a CHAT turn (v1.326.0).

A coding project usually carries its rules in the folder itself: ``AGENTS.md``
(Codex and most agent tools) and ``CLAUDE.md`` (Claude Code), sometimes with a
personal ``*.local.md`` beside them. Until this release only a SCHEDULE read a
folder's rules file (``scheduling/knobs.folder_rules_block``, AGENTS.md /
.ironjarvis.md, first found); a chat grounded in the very same project never
saw them, so the model answered against rules the user had already written.

:func:`read_rule_files` is the ONE reader. It takes the folder and the file
names to look for, and hands back what may be injected:

* the folder must be USABLE FOR READING: absolute, an existing directory, and
  allowed by the file policy (``fs_policy.fs_read_ok`` — never a protected
  root, never outside ``IRONJARVIS_FS_ALLOWLIST``). No writability probe: the
  turn only reads, and a probe creates a file.
* each file is a regular file the policy allows (a link is judged by where it
  RESOLVES), read up to :data:`READ_CHARS` characters as UTF-8 (bad bytes
  replaced), and skipped when blank;
* files with the same CONTENT are read once (a ``CLAUDE.md`` that is a copy of
  or a link to ``AGENTS.md`` is common), the first name in order wins;
* the whole set shares ONE character budget (``total_cap``): when the files do
  not fit, each gets a fair share (a small file keeps all of its text, the
  rest is split among the larger ones) and a file over its share is cut
  head 2/3 + tail 1/3 around a visible ``[… middle trimmed: N characters …]``
  line (``promptguard.cap_text``);
* every file goes through ``promptguard`` (source ``<source_prefix> <name>``):
  a line that tries to take over the model becomes ``[BLOCKED: … — removed
  from …]``, the rest of the file still loads, and one ``context.blocked``
  event is published per (session, source) when a bus is given.

BLOCKING (stat + read): callers hop it off the event loop
(``asyncio.to_thread``, the v1.153.1 rule). It NEVER raises: a folder or a
file that cannot be read is simply not used.

:func:`chat_folder_rules_block` renders the chat's section — one fenced,
labelled ``# Project folder instructions (<file>)`` block per file, in order —
and the list of file names actually used (the turn's ``folder_rules`` receipt).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: The files a CHAT turn reads from its project's folder, in this order.
CHAT_RULE_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    "AGENTS.local.md",
    "CLAUDE.local.md",
)
#: Characters shared by every file a chat turn injects (the headers and
#: fences ride on top, a few dozen characters each).
CHAT_RULES_CAP = 8_000
#: How much of one file is read at all; the shared cap trims it further.
READ_CHARS = 64_000
#: A UTF-8 character is at most 4 bytes, so this many bytes always hold
#: :data:`READ_CHARS` characters.
_READ_BYTES = READ_CHARS * 4
#: The source label promptguard names in a placeholder and an event.
CHAT_SOURCE_PREFIX = "project folder"
#: The setting that turns the chat read off (``settings/schema.py``).
SETTING_KEY = "chat_folder_rules"

_BACKTICKS = re.compile(r"`+")


@dataclass(frozen=True)
class RuleFile:
    """One file that may be injected. ``text`` is the scanned, capped text."""

    name: str
    text: str
    truncated: bool = False
    blocked: list[dict[str, str]] = field(default_factory=list)


def chat_folder_rules_enabled(config: Any) -> bool:
    """The ``chat_folder_rules`` setting, read LIVE on every turn. Default on;
    a config without the field (an older test double) counts as on."""
    try:
        return bool(getattr(config, SETTING_KEY, True))
    except Exception:  # noqa: BLE001 — a broken config never costs the turn
        return True


def _usable_folder(root: Any) -> Path | None:
    """The folder as a Path when a turn may READ in it, else None."""
    from ..core.fs_policy import fs_read_ok

    raw = str(root or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute() or not p.is_dir():
        return None
    ok, _why = fs_read_ok(str(p))
    return p if ok else None


def _read_one(path: Path) -> str | None:
    """The file's text (at most :data:`READ_CHARS` characters), or None when it
    is missing, not a regular file, refused by the file policy, or unreadable."""
    from ..core.fs_policy import fs_read_ok

    try:
        if not path.is_file():
            return None
        ok, _why = fs_read_ok(str(path))
        if not ok:
            return None
        with open(path, "rb") as fh:
            data = fh.read(_READ_BYTES)
    except OSError:
        log.debug("folder rules: %s unreadable", path, exc_info=True)
        return None
    text = data.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    # Newlines as ``read_text`` gives them (a Windows-saved file is CRLF): the
    # scan splits paragraphs on blank LINES, and "\r\n\r\n" is not one to it.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text[:READ_CHARS]


def _shares(lengths: list[int], total: int) -> list[int | None]:
    """Each file's cap under one shared budget: None (no cap) for every file
    when they all fit; else a fair share, smallest files first, so a short
    file keeps all of its text and the room it leaves goes to the others."""
    if total <= 0 or sum(lengths) <= total:
        return [None] * len(lengths)
    caps: list[int | None] = [None] * len(lengths)
    remaining = total
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    for k, i in enumerate(order):
        fair = remaining // (len(order) - k)
        give = min(lengths[i], fair)
        caps[i] = None if give >= lengths[i] else give
        remaining -= give
    return caps


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def read_rule_files(
    root: Any,
    names: Iterable[str],
    *,
    total_cap: int = CHAT_RULES_CAP,
    source_prefix: str = CHAT_SOURCE_PREFIX,
    first_only: bool = False,
    event_bus: Any = None,
    session_id: str | None = None,
) -> list[RuleFile]:
    """The rule files in ``root`` that may be injected, in ``names`` order.

    ``first_only`` stops at the first file that EXISTS (the schedule knob's
    rule: its first file wins even when it is blank). Otherwise every file is
    read, blank ones skipped and same-content ones read once. ``total_cap`` is
    shared by every file kept (see the module docstring). BLOCKING; never
    raises (an unexpected fault reads as "no files")."""
    try:
        return _read_rule_files(
            root, names, total_cap=total_cap, source_prefix=source_prefix,
            first_only=first_only, event_bus=event_bus, session_id=session_id,
        )
    except Exception:  # noqa: BLE001 — the rules must never break a turn
        log.warning("folder rules could not be read; continuing without them", exc_info=True)
        return []


def _read_rule_files(
    root: Any,
    names: Iterable[str],
    *,
    total_cap: int,
    source_prefix: str,
    first_only: bool,
    event_bus: Any,
    session_id: str | None,
) -> list[RuleFile]:
    from ..core.promptguard import publish_blocked, scan_context

    folder = _usable_folder(root)
    if folder is None:
        return []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in names:
        raw = _read_one(folder / name)
        if raw is None:
            continue
        if first_only:
            if _norm(raw):
                found.append((name, raw))
            break
        key = _norm(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        found.append((name, raw))
    if not found:
        return []
    caps = _shares([len(raw) for _n, raw in found], total_cap)
    out: list[RuleFile] = []
    for (name, raw), cap in zip(found, caps):
        result = scan_context(raw, source=f"{source_prefix} {name}", cap=cap)
        if result.blocked:
            publish_blocked(event_bus, session_id, result)
        text = result.text.strip()
        if not text:
            continue
        out.append(
            RuleFile(name=name, text=text, truncated=result.truncated, blocked=list(result.blocked))
        )
    return out


def _fence_for(text: str) -> str:
    """A backtick fence longer than any backtick run inside ``text`` (an
    instruction file usually holds its own code fences)."""
    longest = max((len(m.group(0)) for m in _BACKTICKS.finditer(text)), default=0)
    return "`" * max(3, longest + 1)


#: The one sentence under each heading.
CHAT_RULES_LINE = (
    "This is the file {name} from the project folder. Follow it for work in "
    "this project, unless the user asks for something different."
)


def render_chat_section(files: list[RuleFile]) -> str:
    """``"\\n\\n" + block`` per file, or "" when there are none."""
    parts: list[str] = []
    for f in files:
        fence = _fence_for(f.text)
        parts.append(
            f"\n\n# Project folder instructions ({f.name})\n"
            + CHAT_RULES_LINE.format(name=f.name)
            + f"\n{fence}markdown\n{f.text}\n{fence}"
        )
    return "".join(parts)


def chat_folder_rules_block(
    root: Any, *, event_bus: Any = None, session_id: str | None = "chat"
) -> tuple[str, list[str]]:
    """``(section, used)`` for a chat turn grounded in a project whose folder
    is ``root``: the fenced sections ("" when nothing was usable) and the file
    names actually injected (``[]`` when none). BLOCKING; never raises."""
    try:
        files = read_rule_files(
            root, CHAT_RULE_FILES, total_cap=CHAT_RULES_CAP,
            source_prefix=CHAT_SOURCE_PREFIX, event_bus=event_bus, session_id=session_id,
        )
        return render_chat_section(files), [f.name for f in files]
    except Exception:  # noqa: BLE001 — the rules must never break a turn
        log.warning("folder rules section failed; continuing without it", exc_info=True)
        return "", []


__all__ = [
    "CHAT_RULES_CAP",
    "CHAT_RULES_LINE",
    "CHAT_RULE_FILES",
    "CHAT_SOURCE_PREFIX",
    "READ_CHARS",
    "RuleFile",
    "SETTING_KEY",
    "chat_folder_rules_block",
    "chat_folder_rules_enabled",
    "read_rule_files",
    "render_chat_section",
]
