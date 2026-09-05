"""Lenient JSON recovery for MODEL OUTPUT (v1.225.0).

Local models — the daily driver's default is a fleet endpoint — hand back
"almost JSON" all the time: a fenced block, prose around the object, a
trailing comma, single quotes, a reply that names the object and then writes
it. Every caller that used to do ``json.loads`` and give up on failure
silently turned those near-misses into "no tool call" / "no workflow", which
the user experienced as chat workflows being unreliable.

One recovery ladder, in one place, so every caller degrades the same way:

1. ``json.loads`` as-is;
2. the first fenced block that parses;
3. the FIRST balanced ``{…}`` or ``[…]``, found with a string-aware scan
   (braces inside strings do not count; an unterminated object fails);
4. the same candidates after the two repairs models actually make —
   trailing commas before ``}``/``]`` and single-quoted keys/strings.

Nothing here invents content: a candidate either parses or is rejected, and
``None`` is the honest answer when none does.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_SINGLE_QUOTED = re.compile(r"'([^'\\\n]*(?:\\.[^'\\\n]*)*)'")


def _try(text: str) -> Any | None:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _repaired(text: str) -> str:
    fixed = _TRAILING_COMMA.sub(r"\1", text)
    if '"' not in fixed and "'" in fixed:
        fixed = _SINGLE_QUOTED.sub(lambda m: '"' + m.group(1).replace('"', '\\"') + '"', fixed)
    return fixed


def first_balanced(text: str, opener: str = "{") -> str | None:
    """The first balanced ``{…}`` (or ``[…]``) in *text*, string-aware.
    Returns the raw slice, or None when no complete one exists."""
    closer = "}" if opener == "{" else "]"
    start, depth, in_str, escape = -1, 0, False, False
    for i, ch in enumerate(text):
        if start < 0:
            if ch == opener:
                start, depth = i, 1
            continue
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
        elif in_str:
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def loads_lenient(text: Any, *, want: type | tuple[type, ...] = (dict, list)) -> Any | None:
    """Recover a JSON value of type *want* from model text, or None."""
    if isinstance(text, want):
        return text
    if not isinstance(text, str) or not text.strip():
        return None
    candidates: list[str] = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCE.finditer(text)]
    for opener in ("{", "["):
        raw = first_balanced(text, opener)
        if raw:
            candidates.append(raw)
    for cand in candidates:
        for attempt in (cand, _repaired(cand)):
            obj = _try(attempt)
            if isinstance(obj, want):
                return obj
    return None


def loads_object(text: Any) -> dict | None:
    """A JSON OBJECT recovered from model text, or None."""
    obj = loads_lenient(text, want=dict)
    return obj if isinstance(obj, dict) else None


#: JSON-schema scalar/container names → the Python types a tool argument of
#: that declared type may arrive as. ``bool`` is a subclass of ``int`` in
#: Python, so integer/number exclude it explicitly (``true`` is not a count).
JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def json_type_ok(value: Any, declared: Any) -> bool:
    """Cheap TOP-LEVEL check of *value* against a schema ``type`` name.

    Only the six plain names are judged; anything else (a list of types,
    ``null``, no type at all) is accepted — this is a guardrail for the
    model's most common slip, not a validator. A STRING is accepted for every
    declared type: models stringify ("5", "true", a newline-separated list)
    and tools coerce those on purpose (``worklist_add`` takes ``items`` as
    text, v1.174.0) — refusing them here would undo that leniency. What is
    refused is a value that cannot be what the schema says: a number for a
    path, a list for a string, a dict for an array.
    """
    kinds = JSON_TYPES.get(declared) if isinstance(declared, str) else None
    if kinds is None or isinstance(value, str):
        return True
    if isinstance(value, bool) and bool not in kinds:
        return False
    return isinstance(value, kinds)


def unwrap_arguments(obj: Any) -> Any:
    """Peel the ``{"arguments": …}`` ENVELOPE a local model wraps a tool call in.

    Live (2026-08-03 ``file_search``, 2026-08-16 ``shell``): the model put its
    real arguments one level down — ``{"arguments": "{\\"query\\": \\"*\\"}"}``
    — mirroring the wire field name. That is valid JSON, so ``json.loads``
    succeeded, the recovery ladder never ran, and the tool crashed on
    ``KeyError: 'query'``. An object whose ONLY key is ``arguments`` holding a
    dict (or a string that :func:`loads_object` turns into one) is the
    envelope, never the arguments: return the inner dict. Anything else comes
    back untouched — a tool that genuinely takes an ``arguments`` parameter
    alongside others is not affected.
    """
    if not isinstance(obj, dict) or set(obj) != {"arguments"}:
        return obj
    inner = obj["arguments"]
    if isinstance(inner, dict):
        return inner
    if isinstance(inner, str):
        loaded = loads_object(inner)
        if isinstance(loaded, dict):
            return loaded
    return obj
