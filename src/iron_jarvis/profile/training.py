"""Train Jarvis on me — writing samples in, a voice card out (v1.145.0).

The store half is ordinary. The derivation half has three rules that are the
whole reason this is a separate module rather than a prompt inline in a route:

1. **Real provider or nothing.** A fabricated voice card would be injected into
   every prompt from then on — it is the single worst thing in this app to
   invent. The route rides ``d._skill_distill_complete()``, which returns
   ``None`` when only the offline mock is reachable (the same gate the skill
   distiller and the memory importer use), and the endpoint then 400s honestly.
2. **Style, never content.** The card lands in every system prompt, so a card
   that says "writes about the Henderson audit" would leak a client name into
   every future request — including ones routed to a cloud model. The prompt
   forbids topics, names, and facts; :func:`clean_card` strips any line that
   looks like it smuggled one through anyway.
3. **Suggest, don't act.** :func:`derive` returns a PROPOSAL. Nothing is stored
   until the user saves it from /train — the same suggest-don't-act discipline
   the memory steward and the skill learner already follow.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from .models import WritingSampleRecord

#: Caps. Generous enough for real writing, bounded enough that the table cannot
#: become a document store by accident.
MAX_SAMPLES = 20
MAX_SAMPLE_CHARS = 20_000

#: What actually goes to the model. Style is legible in far less text than
#: people assume, and every extra thousand characters is latency + spend on a
#: call the user is waiting on. Samples are sliced EVENLY so five short emails
#: and one long essay both get represented — taking the first N characters
#: overall would let one long sample crowd out the others entirely.
DERIVE_CHAR_BUDGET = 18_000

#: Below this, there is not enough writing to describe a voice honestly.
MIN_DERIVE_CHARS = 400

#: The model is told to answer with this exact token when the samples are too
#: thin or too inconsistent — better an honest "no" than an invented card.
NO_SIGNAL = "NOT ENOUGH SIGNAL"

DERIVE_SYSTEM = (
    "You are analysing HOW one person writes, so another writer can match their "
    "voice.\n\n"
    "You will be given samples of their own writing. Describe their STYLE.\n\n"
    "Cover only what the samples actually show:\n"
    "- typical sentence length and rhythm\n"
    "- vocabulary level, and any words or phrases they reach for repeatedly\n"
    "- how they open and how they close\n"
    "- punctuation and formatting habits (dashes, lists, capitalisation, emoji)\n"
    "- what they never do\n\n"
    "RULES:\n"
    "- 4 to 8 short lines. One instruction per line. No preamble, no headings, "
    "no numbering.\n"
    "- Write each line as guidance to a writer, e.g. 'Short declarative "
    "sentences; rarely more than 15 words.'\n"
    "- STYLE ONLY. Never mention what the samples were about, and never repeat "
    "a name, company, client, place, product, or any other fact from them.\n"
    f"- If the samples are too short or too inconsistent to tell, reply with "
    f"exactly: {NO_SIGNAL}"
)

#: Lines that clearly describe CONTENT rather than style get dropped by
#: :func:`clean_card` — rule 2's belt-and-braces. Deliberately narrow: it
#: catches the model NARRATING the samples, not ordinary style vocabulary.
_CONTENT_LINE = re.compile(
    r"\b(writes about|topics? (include|are)|discusses|the samples? (are|were|"
    r"describe)|mentions (a|the)|subject matter)\b",
    re.I,
)

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_HEADING = re.compile(r"^\s*#{1,6}\s*")
_FENCE = re.compile(r"^\s*```")


class SampleStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self) -> list[WritingSampleRecord]:
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(select(WritingSampleRecord).order_by(WritingSampleRecord.created_at))
            )
            return [WritingSampleRecord(**r.model_dump()) for r in rows]

    def add(self, *, label: str, text: str, origin: str = "pasted") -> WritingSampleRecord:
        """Store one sample. Raises ValueError on an empty sample or a full store."""
        text = (text or "").strip()
        if not text:
            raise ValueError("that sample is empty")
        if len(self.list()) >= MAX_SAMPLES:
            raise ValueError(
                f"that is the {MAX_SAMPLES}-sample limit — remove one first "
                f"(more samples past this point stop improving the result)"
            )
        rec = WritingSampleRecord(
            label=(label or "").strip()[:120] or "sample",
            text=text[:MAX_SAMPLE_CHARS],
            origin=(origin or "pasted")[:200],
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return WritingSampleRecord(**rec.model_dump())

    def delete(self, sample_id: str) -> bool:
        with session_scope(self.engine) as db:
            row = db.get(WritingSampleRecord, sample_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True


def as_dict(rec: WritingSampleRecord) -> dict[str, Any]:
    """The wire shape. The full text is NOT sent — a list of samples is a list,
    and shipping 20 × 20k characters to render a list is how a page gets slow."""
    return {
        "id": rec.id,
        "label": rec.label,
        "origin": rec.origin,
        "chars": len(rec.text or ""),
        "excerpt": (rec.text or "")[:280],
        "created_at": rec.created_at,
    }


def build_prompt(samples: list[WritingSampleRecord]) -> str:
    """The user-side prompt: every sample, sliced to an EVEN share of the
    budget (see :data:`DERIVE_CHAR_BUDGET`)."""
    usable = [s for s in samples if (s.text or "").strip()]
    if not usable:
        return ""
    share = max(400, DERIVE_CHAR_BUDGET // len(usable))
    parts = []
    for i, s in enumerate(usable, 1):
        text = (s.text or "").strip()
        clipped = text[:share]
        if len(text) > share:
            clipped += "\n[…]"
        parts.append(f"--- SAMPLE {i} ({s.label or 'sample'}) ---\n{clipped}")
    return "\n\n".join(parts)


def clean_card(raw: str) -> str:
    """Turn the model's reply into a storable card, or "" when it declined.

    Strips fences/headings/bullet markers so the card is plain lines (the block
    renderer already adds its own framing), drops content-narrating lines (rule
    2), and caps the result.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if NO_SIGNAL.lower() in text.lower():
        return ""
    lines: list[str] = []
    for line in text.splitlines():
        # A fence or a heading is STRUCTURE, not an instruction: the block
        # renderer supplies the framing, so a stray "## Voice" line would just
        # be noise inside it. Dropped whole rather than unwrapped.
        if _FENCE.match(line) or _HEADING.match(line):
            continue
        line = _BULLET.sub("", line).strip()
        if not line:
            continue
        if _CONTENT_LINE.search(line):
            continue
        lines.append(line)
        if len(lines) >= 8:
            break
    return "\n".join(lines)[:900].strip()


def too_thin(samples: list[WritingSampleRecord]) -> str | None:
    """Why this corpus cannot be derived from yet, or None.

    Checked by the ROUTE *before* the real-provider gate, deliberately: telling
    someone to go connect a model when their actual problem is that they pasted
    two sentences would send them off to fix the wrong thing.
    """
    total = sum(len((s.text or "").strip()) for s in samples)
    if total < MIN_DERIVE_CHARS:
        return (
            f"only {total} characters of writing so far — add roughly "
            f"{MIN_DERIVE_CHARS} or more before this can say anything useful"
        )
    return None


async def derive(complete, samples: list[WritingSampleRecord]) -> tuple[str, str]:
    """``(card, reason)`` — the proposal, or ``("", why not)``.

    ``complete(system, prompt) -> str`` is the REAL-provider callable from
    ``d._skill_distill_complete()``; the route is responsible for refusing when
    that gate hands back ``None`` (rule 1). This function never raises for a
    thin corpus or a declining model — both are honest outcomes, not errors.
    """
    thin = too_thin(samples)
    if thin:
        return ("", thin)
    prompt = build_prompt(samples)
    if not prompt:
        return ("", "no usable samples")
    raw = await complete(DERIVE_SYSTEM, prompt)
    card = clean_card(raw)
    if not card:
        return (
            "",
            "the model could not find a consistent voice in these samples — "
            "try adding more, or samples that sound more like each other",
        )
    return (card, "")
