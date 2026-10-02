"""The prompt-injection scan of CONTEXT TEXT before it rides a system prompt
(v1.298.0).

Everything the app injects into a system prompt that somebody other than the
user may have written — a project knowledge file, an agent's own notebook, a
skill pulled in from ``~/.claude``, an attachment, a retrieved memory, a
distilled lesson — is DATA that the model will read as if it were ours. Until
this wave none of those seams was scanned; the only detector,
``computeruse/safety.detect_injection``, guarded the browser and the MCP
results. This module puts that detector in front of every context seam.

What :func:`scan_context` does, in order (the hermes precedent):

1. CAP — ``cap`` characters, head 2/3 + tail 1/3 around a trim marker, so a
   100 MB paste cannot be used to push the real instructions out of the
   window (``agents/files.trim_head_tail`` is the same shape).
2. DEFANG — a ``[UNTRUSTED CONTENT …]`` / ``[END UNTRUSTED …]`` fence marker
   INSIDE the text is not ours; its ``[`` becomes ``(`` (``memory/steward``'s
   ``_defang``) so it still reads as what the author wrote but can no longer
   close or open a fence. Defanged, not blocked.
3. SCAN by PARAGRAPH — blank-line separated; a paragraph over
   ``_PIECE_CHARS`` is split further on sentence/line boundaries. Each piece
   goes through ``detect_injection``; a flagged piece is replaced by
   ``[BLOCKED: <category> — removed from <source>]`` and every other byte is
   kept exactly. A flagged piece that spans several lines (a notebook, a
   bullet list) is refined to its LINES first, so only the poisoned line
   goes. Consecutive placeholders collapse to one. The REST of the file
   still loads: one poisoned paragraph does not cost the user the document.

Pure: no I/O, no model, and :func:`scan_context` NEVER raises — on any
internal failure it returns the text unchanged (clean) rather than blocking a
turn, because a scanner that crashes the lane is a denial of service the
attacker did not even have to write.

:func:`publish_blocked` tells the ledger: ONE ``context.blocked`` event per
(session, source) per process (the detections bell's dedupe), from sync code
on or off the loop and from the async lanes alike (see its docstring).

The user's OWN words are not scanned here: the seams decide. A skill the user
wrote (``source`` builtin/user, ``created_by`` user) is injected verbatim —
see ``skills/framework.inject``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Default character cap on one context text (head 2/3 + tail 1/3).
CONTEXT_CAP = 20_000
#: A paragraph longer than this is split on sentence/line boundaries so the
#: detector's bounded regexes see a piece, never a wall.
_PIECE_CHARS = 1_500
#: Longest masked excerpt a finding carries.
_EXCERPT_CHARS = 80

#: Paragraph separators, CAPTURED so the text can be rebuilt byte-identical.
_PARA_SEP = re.compile(r"(\n[ \t]*\n+)")
#: Sentence / line boundaries inside an oversized paragraph, captured too.
_SENT_SEP = re.compile(r"((?<=[.!?])[ \t]+|\n)")
#: The fence markers the app's own untrusted wrapper uses (``computeruse/
#: safety.wrap_untrusted``); copied from ``memory/steward._FENCE_MARKER``.
_FENCE_MARKER = re.compile(r"\[\s*(?:/\s*)?(?:END\s+)?UNTRUSTED\s+CONTENT", re.IGNORECASE)
#: What a removed piece reads as.
_PLACEHOLDER = "[BLOCKED: {category} — removed from {source}]"


@dataclass
class ScanResult:
    """What :func:`scan_context` hands back. ``text`` is what may be injected."""

    text: str
    blocked: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "blocked": [dict(b) for b in self.blocked],
            "truncated": self.truncated,
            "source": self.source,
        }


def placeholder(category: str, source: str) -> str:
    """The exact placeholder a flagged piece becomes (tests pin it)."""
    return _PLACEHOLDER.format(category=category or "injection", source=source or "context")


# --------------------------------------------------------------------------- #
# the pure pipeline
# --------------------------------------------------------------------------- #


def _coerce(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    if isinstance(text, str):
        return text
    try:
        return str(text)
    except Exception:  # noqa: BLE001
        return ""


def cap_text(text: str, cap: int | None = CONTEXT_CAP) -> tuple[str, bool]:
    """``(text, truncated)``: unchanged when it fits (or ``cap`` is None/<=0),
    else head 2/3 + tail 1/3 around ``[… middle trimmed: N characters …]``,
    the whole result EXACTLY ``cap`` characters long."""
    if not cap or cap <= 0 or len(text) <= cap:
        return text, False
    removed = len(text) - cap
    for _ in range(4):  # the digit count of N settles in one or two passes
        marker = f"\n[… middle trimmed: {removed} characters …]\n"
        room = max(0, cap - len(marker))
        head = room * 2 // 3
        tail = room - head
        actual = len(text) - head - tail
        if actual == removed:
            break
        removed = actual
    out = text[:head] + marker + (text[-tail:] if tail else "")
    return out[:cap], True


def defang(text: str) -> str:
    """Neutralise fence markers inside context text: ``[`` → ``(`` on each
    marker. Nothing is hidden; the marker just stops being a delimiter."""
    return _FENCE_MARKER.sub(lambda m: "(" + m.group(0)[1:], text or "")


def _sentences(piece: str) -> list[str]:
    """``piece`` as a list of [chunk, sep, chunk, …] with every chunk at most
    about ``_PIECE_CHARS`` long (sentences are packed greedily; a single
    sentence longer than that stays whole — the regexes are line-bounded)."""
    parts = _SENT_SEP.split(piece)
    out: list[str] = []
    cur = ""
    cur_sep = ""
    i = 0
    while i < len(parts):
        chunk = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if cur and len(cur) + len(cur_sep) + len(chunk) > _PIECE_CHARS:
            out.append(cur)
            out.append(cur_sep)
            cur, cur_sep = chunk, sep
        else:
            cur = cur + cur_sep + chunk
            cur_sep = sep
        i += 2
    out.append(cur + cur_sep)  # a trailing separator folds in: parity holds
    return out


def _pieces(text: str) -> list[str]:
    """[content, sep, content, sep, …] — concatenating gives ``text`` back."""
    parts = _PARA_SEP.split(text)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # a captured separator
            out.append(part)
            continue
        if len(part) > _PIECE_CHARS:
            out.extend(_sentences(part))
        else:
            out.append(part)
    return out


def _excerpt(piece: str) -> str:
    """A MASKED, whitespace-folded excerpt: masked before clipping, so a
    secret can never be cut into a shape the mask no longer recognises."""
    try:
        from ..detections.redact import mask
    except Exception:  # noqa: BLE001 — the masker must never be the failure
        mask = None  # type: ignore[assignment]
    flat = re.sub(r"\s+", " ", piece).strip()
    if mask is not None:
        try:
            flat = mask(flat)
        except Exception:  # noqa: BLE001
            flat = ""
    return flat[:_EXCERPT_CHARS]


def _mask_reason(reason: str) -> str:
    try:
        from ..detections.redact import mask

        return mask(reason)
    except Exception:  # noqa: BLE001
        return ""


def _detect(piece: str) -> dict[str, Any]:
    from ..computeruse.safety import detect_injection

    return detect_injection(piece)


def _units(piece: str) -> list[str]:
    """[unit, sep, unit, …] at sentence/line boundaries — concatenating
    gives ``piece`` back."""
    return _SENT_SEP.split(piece)


#: Longest source label (a file name, a skill name) the placeholder repeats.
_SOURCE_CHARS = 120


def safe_source(source: Any) -> str:
    """The source label as the placeholder and the event may repeat it. A
    label carries a NAME somebody else chose (an attachment's file name, a
    knowledge item's title, a skill's name) — if the name itself is the
    attack, "removed from project knowledge: ignore all previous…" would
    re-plant the sentence through our own words. A flagged label keeps only
    its kind (the part before the first ":" or "(") and says the name was
    withheld; every label is folded to one line and bounded."""
    label = re.sub(r"\s+", " ", _coerce(source)).strip()[:_SOURCE_CHARS] or "context"
    if _detect(label).get("flagged"):
        kind = label.split(":", 1)[0] if ":" in label else label.split(" ", 1)[0]
        return f"{kind.strip() or 'context'}: (name withheld — it was flagged too)"
    return label


def _scan(text: Any, source: str, cap: int | None) -> ScanResult:
    source = safe_source(source)
    raw = _coerce(text)
    body, truncated = cap_text(raw, cap)
    body = defang(body)
    blocked: list[dict[str, str]] = []
    out: list[str] = []
    state = {"sep": "", "last_placeholder": False}

    def emit(part: str, *, refine: bool) -> None:
        """Keep ``part`` or replace it with the placeholder. A flagged piece
        that holds several SENTENCES or LINES (a notebook, a bullet list — no
        blank line anywhere; a long paragraph) is refined to those units, so
        one poisoned sentence does not take the rest with it: every detector
        pattern is line-bounded (``[^.\\n]`` spans, ``(^|\\n)`` anchors), so
        a unit that flags inside the whole flags alone too — and when none
        does, the whole piece goes (never a silent pass)."""
        verdict = _detect(part) if part.strip() else {"flagged": False}
        if not verdict.get("flagged"):
            out.append(state["sep"])
            out.append(part)
            state["sep"] = ""
            if part.strip():
                state["last_placeholder"] = False
            return
        if refine:
            units = _units(part)
            if len(units) > 1 and any(
                _detect(u).get("flagged") for u in units[::2] if u.strip()
            ):
                for j, unit in enumerate(units):
                    if j % 2 == 1:
                        state["sep"] = unit
                    else:
                        emit(unit, refine=False)
                return
        category = str(verdict.get("category") or "injection")
        blocked.append(
            {
                "category": category,
                "reason": _mask_reason(str(verdict.get("reason") or "")),
                "excerpt": _excerpt(part),
            }
        )
        if state["last_placeholder"]:
            state["sep"] = ""  # consecutive placeholders collapse to one
            return
        out.append(state["sep"])
        out.append(placeholder(category, source))
        state["sep"] = ""
        state["last_placeholder"] = True

    for i, part in enumerate(_pieces(body)):
        if i % 2 == 1:
            state["sep"] = part
            continue
        emit(part, refine=True)
    out.append(state["sep"])
    return ScanResult(text="".join(out), blocked=blocked, truncated=truncated, source=source)


def scan_context(text: Any, *, source: str, cap: int | None = CONTEXT_CAP) -> ScanResult:
    """Cap, defang and scan one piece of context text. NEVER raises: on an
    internal failure the text comes back unchanged and unflagged (logged) —
    a scanner must not be the thing that takes the lane down. ``cap=None``
    means no cap (a seam whose own budget already bounds the text)."""
    try:
        return _scan(text, source, cap)
    except Exception:  # noqa: BLE001 — see the docstring
        log.warning("promptguard scan failed for %s; text passed unscanned", source, exc_info=True)
        try:
            return ScanResult(text=_coerce(text), source=source)
        except Exception:  # noqa: BLE001
            return ScanResult(text="", source=source)


# --------------------------------------------------------------------------- #
# publishing — once per (session, source) per process
# --------------------------------------------------------------------------- #

_PUBLISHED: set[tuple[str, str]] = set()
_PUBLISH_LOCK = threading.Lock()
_TASKS: set[Any] = set()


def _event_type() -> str:
    try:
        from .events import EventType

        return str(getattr(EventType, "CONTEXT_BLOCKED", "context.blocked"))
    except Exception:  # noqa: BLE001
        return "context.blocked"


def publish_blocked(event_bus: Any, session_id: str | None, result: ScanResult) -> bool:
    """Publish ONE ``context.blocked`` {session_id, source, count, categories}
    for this (session, source) per process; True when it was published now.

    Shape: ``EventBus.publish`` is a coroutine. On the loop thread (the async
    lanes, or a sync seam the runtime calls without ``to_thread``) it is
    scheduled as a task; off the loop (a seam running under
    ``asyncio.to_thread`` — both chat lanes call ``knowledge.ground`` that
    way) it runs under ``asyncio.run`` like the detections bell does, which
    the bus delivers thread-safely to the subscriber queues' owning loop. A
    sync double (a test bus whose ``publish`` returns None) just returns.
    Never raises.
    """
    try:
        if event_bus is None or not result.blocked:
            return False
        key = (str(session_id or ""), str(result.source or ""))
        with _PUBLISH_LOCK:
            if key in _PUBLISHED:
                return False
            _PUBLISHED.add(key)
        payload = {
            "session_id": key[0],
            "source": key[1],
            "count": len(result.blocked),
            "categories": sorted({str(b.get("category") or "") for b in result.blocked}),
        }
        try:
            outcome = event_bus.publish(_event_type(), payload, session_id=session_id or None)
        except TypeError:  # a bus without the session_id keyword
            outcome = event_bus.publish(_event_type(), payload)
        if not inspect.isawaitable(outcome):
            return True
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            task = running.create_task(outcome)  # type: ignore[arg-type]
            _TASKS.add(task)
            task.add_done_callback(_TASKS.discard)
            return True
        asyncio.run(outcome)  # type: ignore[arg-type]
        return True
    except Exception:  # noqa: BLE001 — telling the ledger is best-effort
        log.debug("context.blocked publish failed", exc_info=True)
        return False


async def flush_published() -> None:
    """Await every publish scheduled on this loop (tests)."""
    pending = [t for t in list(_TASKS) if isinstance(t, asyncio.Future)]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def reset_published() -> None:
    """Forget the per-process dedupe (tests)."""
    with _PUBLISH_LOCK:
        _PUBLISHED.clear()


# --------------------------------------------------------------------------- #
# the one-call seam helper + the project text the runtime reads directly
# --------------------------------------------------------------------------- #


def guard(
    text: Any,
    *,
    source: str,
    event_bus: Any = None,
    session_id: str | None = None,
    cap: int | None = CONTEXT_CAP,
) -> str:
    """Scan ``text`` and, when a bus is at hand, publish once. Returns the
    text that may be injected. Never raises."""
    result = scan_context(text, source=source, cap=cap)
    if result.blocked:
        publish_blocked(event_bus, session_id, result)
    return result.text


def guarded_project_text(
    project: Any,
    *,
    event_bus: Any = None,
    session_id: str | None = None,
    cap: int | None = None,
) -> tuple[str, str]:
    """``(instructions, brief)`` of a project record, stripped and scanned
    under the sources "project instructions" / "project brief". Both chat
    lanes read these here; ``agents/runtime._project_context`` reads the
    record directly and should call this too (same clip afterwards).

    ``cap`` (wave-4a review, PERF): the consumer's own HEAD clip (``[:2000]``
    at every call site), applied BEFORE the scan so a 20k instructions text
    costs a 2k scan — the scan never reads what the prompt will not carry.
    A head clip, not :func:`cap_text`'s head+tail: the consumer keeps the
    head, so the scanned bytes and the injected bytes are the same bytes.
    None = scan the whole text (the old behaviour)."""
    instructions = _coerce(getattr(project, "instructions", "") or "").strip()
    brief = _coerce(getattr(project, "brief", "") or "").strip()
    if cap and cap > 0:
        instructions = instructions[:cap]
        brief = brief[:cap]
    if instructions:
        instructions = guard(
            instructions, source="project instructions", event_bus=event_bus, session_id=session_id
        )
    if brief:
        brief = guard(brief, source="project brief", event_bus=event_bus, session_id=session_id)
    return instructions, brief


__all__ = [
    "CONTEXT_CAP",
    "ScanResult",
    "cap_text",
    "defang",
    "flush_published",
    "guard",
    "guarded_project_text",
    "placeholder",
    "publish_blocked",
    "reset_published",
    "scan_context",
]
