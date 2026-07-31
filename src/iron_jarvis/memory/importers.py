"""Other-model memory import (v1.123.0).

Every major assistant keeps memories about its user, and none of them expose
an API for it — so this module powers the two honest intake lanes:

  paste   the user asks their model "list everything you remember about me"
          and pastes the reply. :func:`parse_memory_dump` handles the list
          shapes assistants actually produce, DETERMINISTICALLY — no model
          call, works offline. Prose that isn't a list returns [] and the
          route falls back to distillation.
  export  the provider's official data export (ChatGPT export zip, Google
          Takeout, Claude export). :func:`extract_export_text` pulls the
          user-side text out of the known shapes; a one-shot model then
          distills durable facts (the route owns that call — identity facts
          must never be fabricated, so offline refuses instead of guessing).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

#: Bounds: a pasted dump or distilled list can't flood the store in one click.
MAX_FACTS = 200
MAX_FACT_CHARS = 500
#: Distillation input budget (mirrors the /remember clip).
MAX_EXPORT_INPUT = 24_000

_BULLET_RX = re.compile(
    r"""^\s*(?:
        [-*•▪–—◦‣∙·]\s+        # bullet glyphs (incl. nested-list glyphs)
      | \d{1,3}[.)]\s+          # 1.  2)  numbered
      | \[\s?[xX ]?\]\s+        # [ ] / [x] checklists
    )(?P<body>.+)$""",
    re.VERBOSE,
)
_BOLD_RX = re.compile(r"\*\*(.+?)\*\*")
# A SECTION header is a short label ("Work:", "## Preferences:") — a full
# sentence ending in a colon ("Here's everything I remember about you:") is
# preamble, not a section, so the label is capped at 3 words and screened for
# sentence-words ("What I remember:" is preamble, not a section).
_HEADER_RX = re.compile(r"^\s*(?:#{1,6}\s+)?(?:[^.:\s]+\s?){1,3}:\s*$")
_NOT_A_SECTION = {"i", "you", "we", "me", "my", "your", "about", "remember", "know", "what", "here", "heres"}


def _clean(fact: str) -> str:
    fact = _BOLD_RX.sub(r"\1", fact).strip()
    fact = re.sub(r"\s+", " ", fact)
    return fact[:MAX_FACT_CHARS]


def parse_memory_dump(text: str) -> list[str]:
    """Parse a pasted "what you remember about me" reply into fact strings.

    Handles bullets, numbered lists, checklists, and indented continuation
    lines; section headers ("Work:") become a prefix for the facts under
    them. Returns [] when the text doesn't LOOK like a list — the caller
    then routes long prose to model distillation instead of guessing here.
    """
    lines = (text or "").splitlines()
    facts: list[str] = []
    section = ""
    for raw in lines:
        if not raw.strip():
            continue
        m = _BULLET_RX.match(raw)
        if m:
            body = _clean(m.group("body"))
            if len(body) >= 8:
                facts.append(f"{section}{body}" if section else body)
            continue
        if _HEADER_RX.match(raw):
            label = _clean(raw).rstrip(":").lstrip("# ").strip()
            words = {w.strip(".,'’").lower() for w in label.split()}
            if words & _NOT_A_SECTION:
                # "What I remember:" — preamble wearing a colon, not a section.
                section = ""
                continue
            # "Work:" / "## Preferences:" — carries onto following bullets.
            section = f"{label}: " if label else ""
            continue
        if facts and (raw.startswith((" ", "\t"))):
            # Indented continuation of the previous bullet.
            facts[-1] = _clean(facts[-1] + " " + raw.strip())
            continue
        # A non-bullet, non-header, non-continuation line: preamble/prose
        # ("Here's everything I remember:") — ignore it AND reset the section,
        # or a "Work:" header would keep stamping facts under later blocks.
        section = ""
    return facts[:MAX_FACTS]


# --------------------------------------------------------------------------- #
# export extraction — provider shapes, best-effort and honest
# --------------------------------------------------------------------------- #


def _chatgpt_conversations(data: list) -> str:
    """ChatGPT export ``conversations.json``: [{title, mapping: {id: {message:
    {author: {role}, content: {parts: [...]}}}}}]. Facts about the USER live
    overwhelmingly in user turns, so those get the budget."""
    chunks: list[str] = []
    total = 0
    for conv in data:
        if total >= MAX_EXPORT_INPUT:
            break
        title = str(conv.get("title") or "").strip()
        user_texts: list[str] = []
        mapping = conv.get("mapping") or {}
        for node in mapping.values():
            msg = (node or {}).get("message") or {}
            role = ((msg.get("author") or {}).get("role") or "").lower()
            if role != "user":
                continue
            parts = (msg.get("content") or {}).get("parts") or []
            for p in parts:
                if isinstance(p, str) and p.strip():
                    user_texts.append(p.strip()[:800])
        if not user_texts:
            continue
        block = f"## {title}\n" + "\n".join(user_texts[:6])
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks)[:MAX_EXPORT_INPUT]


def _claude_conversations(data: list) -> str:
    """Claude export ``conversations.json``: [{name, chat_messages:
    [{sender, text}]}]."""
    chunks: list[str] = []
    total = 0
    for conv in data:
        if total >= MAX_EXPORT_INPUT:
            break
        name = str(conv.get("name") or "").strip()
        texts = [
            str(m.get("text") or "").strip()[:800]
            for m in conv.get("chat_messages") or []
            if str(m.get("sender") or "").lower() in ("human", "user")
            and str(m.get("text") or "").strip()
        ]
        if not texts:
            continue
        block = f"## {name}\n" + "\n".join(texts[:6])
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks)[:MAX_EXPORT_INPUT]


def _takeout_activity(data: list) -> str:
    """Google Takeout (Gemini) ``MyActivity.json``: [{title: "Prompted …",
    …}] — the prompts are the user-side text."""
    prompts: list[str] = []
    for item in data:
        title = str((item or {}).get("title") or "")
        if title.lower().startswith("prompted"):
            prompts.append(title[len("Prompted") :].strip(" :")[:800])
    return "\n".join(prompts)[:MAX_EXPORT_INPUT]


def _classify_and_extract(name: str, payload: bytes) -> str | None:
    """Extract user-side text from one file inside an export, or None."""
    low = name.lower()
    if low.endswith(".json"):
        try:
            data = json.loads(payload.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else {}
            if "mapping" in first:
                return _chatgpt_conversations(data)
            if "chat_messages" in first:
                return _claude_conversations(data)
            if "title" in first and "conversations" not in low:
                if any(
                    str(d.get("title", "")).lower().startswith("prompted")
                    for d in data[:20]
                    if isinstance(d, dict)
                ):
                    return _takeout_activity(data)
        return None
    if low.endswith((".txt", ".md")):
        return payload.decode("utf-8", errors="replace")[:MAX_EXPORT_INPUT]
    return None


def extract_export_text(path: str | Path) -> tuple[str, str]:
    """Pull user-side text out of a provider export file.

    Returns ``(text, detected)`` where detected names what was recognized
    ("chatgpt", "claude", "gemini", "text") — or raises ValueError with an
    honest message when nothing in the file is recognizable.
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"no such file: {p}")
    skipped_big = False
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            infos = [
                i for i in z.infolist()
                if i.filename.lower().endswith((".json", ".txt", ".md"))
            ]
            # Prefer the known transcript files over stray text — and read
            # members LAZILY, one at a time: a heavy export must not buffer
            # every member into RAM before any classification happens.
            infos.sort(
                key=lambda i: (
                    0 if "conversations.json" in i.filename.lower() else
                    1 if "myactivity" in i.filename.lower() else 2
                )
            )
            for info in infos:
                if info.file_size > 200 * 1024 * 1024:
                    skipped_big = True
                    continue
                try:
                    payload = z.read(info)
                except Exception:  # noqa: BLE001 — truncated/corrupt member
                    continue
                got = _try_member(info.filename, payload)
                if got is not None:
                    return got
    else:
        got = _try_member(p.name, p.read_bytes())
        if got is not None:
            return got
    raise ValueError(
        "couldn't find conversations in this file — expected a ChatGPT/Claude "
        "data-export zip (conversations.json), a Google Takeout, or a plain "
        "text/markdown file"
        + (
            "; files inside over 200 MB were skipped"
            if skipped_big
            else ". If this is a data export, the download may be incomplete "
            "or corrupted"
        )
    )


def _try_member(name: str, payload: bytes) -> tuple[str, str] | None:
    text = _classify_and_extract(name, payload)
    if not (text and text.strip()):
        return None
    low = name.lower()
    detected = (
        "gemini" if "myactivity" in low
        else "chatgpt" if _looks_chatgpt(payload)
        else "claude" if _looks_claude(payload)
        else "text"
    )
    return text, detected


def _looks_chatgpt(payload: bytes) -> bool:
    head = payload[:4000].decode("utf-8", errors="replace")
    return '"mapping"' in head


def _looks_claude(payload: bytes) -> bool:
    head = payload[:4000].decode("utf-8", errors="replace")
    return '"chat_messages"' in head


#: The one-shot system prompt the route uses over extracted export text.
DISTILL_SYSTEM = (
    "You extract what an AI assistant would durably REMEMBER about its user "
    "from their conversation history. Output ONLY a plain list, one memory "
    "per line, each starting with '- ': identity (role, location, family), "
    "stable preferences, ongoing projects, constraints, tools they use, and "
    "important people — written as durable facts ('User is …', 'User "
    "prefers …'). NEVER invent or embellish: every fact must be directly "
    "supported by the text. Skip one-off requests and transient details. "
    "At most 60 lines."
)
