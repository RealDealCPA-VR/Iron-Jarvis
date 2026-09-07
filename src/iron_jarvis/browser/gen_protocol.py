"""Generate ``extensions/chrome/src/protocol.ts`` from the Python protocol.

Run as::

    uv run python -m iron_jarvis.browser.gen_protocol

Why the generator is Python and not a Node script: only Python can *import* the
real constants. ``extensions/chrome/scripts/gen-protocol.mjs`` would have to
re-read and re-parse them, and a parser that misses a constant emits a file that
looks complete — which is the drift the generation exists to make impossible. So
this module imports :mod:`iron_jarvis.browser.protocol` and
:mod:`iron_jarvis.browser.errors` and restates nothing: every exported name,
value, error code, remedy string and frame field below is read off the live
objects, and the ``UPPER_CASE`` constants are discovered by walking
``protocol.__all__`` rather than listed here. A constant added to the protocol
therefore appears in the TypeScript on the next generation with no edit here, and
``tests/test_browser_protocol_v1235.py`` fails until that generation is committed.

Determinism is a requirement, not a nicety: the drift test compares bytes. Sets
are never emitted directly (the protocol module already sorts the vocabularies
into tuples), dict order is the source module's insertion order, and the reader
normalises CRLF because GitHub's Windows runners check the committed file out
with ``\\r\\n`` — the trap that took the v1.232.0 installer down.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NotRequired, get_args, get_origin, get_type_hints

from . import protocol as P
from .errors import REMEDIES, BrowserErrorCode

#: ``<repo root>/extensions/chrome/src/protocol.ts``.
#:
#: Resolved from this file rather than from the working directory: the generator
#: is run by a developer from anywhere and by the test suite from the repo root,
#: and a relative path would quietly write a second protocol.ts into whichever
#: directory the caller happened to be in.
OUTPUT_PATH = Path(__file__).resolve().parents[3] / "extensions" / "chrome" / "src" / "protocol.ts"

HEADER = """// GENERATED FILE — DO NOT EDIT.
//
// Written by `uv run python -m iron_jarvis.browser.gen_protocol` from
// src/iron_jarvis/browser/protocol.py, which is the single source of truth for
// the Browser Bridge wire protocol.
//
// A hand edit here is reverted by the next generation and fails
// tests/test_browser_protocol_v1235.py, which regenerates this file into a
// buffer and compares it byte for byte. Change protocol.py and regenerate.
"""

#: Every ``TypedDict`` the generator emits, frames first and then the payload
#: shapes that ride inside them. Two tuples in ``protocol.py`` rather than one,
#: because ``FRAME_SHAPES`` must map a ``type`` string to every frame shape and a
#: params object has no ``type``; joined here because ``_ts_type`` has to resolve a
#: reference to any of them by name, and a shape it cannot resolve is a field whose
#: real type would silently vanish from the generated file.
ALL_SHAPES: tuple[type, ...] = tuple(P.FRAME_TYPEDDICTS) + tuple(P.RESULT_TYPEDDICTS)

#: Python scalar/container types -> their TypeScript spelling.
_SCALARS: dict[Any, str] = {
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
    type(None): "null",
    Any: "unknown",
}


def _ts_type(annotation: Any) -> str:
    """Render one resolved Python annotation as a TypeScript type.

    Deliberately narrow: it handles the shapes the frame ``TypedDict``s actually
    use and raises :class:`TypeError` on anything else. A permissive fallback to
    ``unknown`` would let a new field's real type disappear from the generated
    file while the drift test stayed green — the field would exist on both sides
    and be typed on neither.
    """
    if annotation in _SCALARS:
        return _SCALARS[annotation]
    origin = get_origin(annotation)
    if origin is None:
        name = getattr(annotation, "__name__", "")
        if name in {shape.__name__ for shape in ALL_SHAPES}:
            return name
        raise TypeError(f"no TypeScript spelling for annotation {annotation!r}")
    args = get_args(annotation)
    if origin is dict:
        key, value = args
        if key is not str:
            raise TypeError(f"only str-keyed dicts cross the wire, got {annotation!r}")
        return f"Record<string, {_ts_type(value)}>"
    if origin is list or origin is tuple:
        inner = args[0] if args else Any
        return f"{_ts_type(inner)}[]"
    # `X | None` and other unions.
    if args:
        return " | ".join(_ts_type(arg) for arg in args)
    raise TypeError(f"no TypeScript spelling for annotation {annotation!r}")


def _ts_value(value: Any) -> str:
    """Render one Python constant as a TypeScript literal.

    ``json.dumps`` does the escaping, so a remedy sentence containing a quote or
    a backslash cannot break the generated module — a hand-rolled quoting pass is
    how a generated file ends up syntactically invalid on exactly one string.
    """
    if isinstance(value, BrowserErrorCode):
        return json.dumps(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (tuple, list)):
        items = ", ".join(_ts_value(item) for item in value)
        return f"[{items}] as const"
    if isinstance(value, dict):
        pairs = ", ".join(
            f"{_ts_value(_key_text(key))}: {_ts_value(item)}" for key, item in value.items()
        )
        return f"{{ {pairs} }}"
    raise TypeError(f"no TypeScript literal for {value!r}")


def _key_text(key: Any) -> str:
    """The string form of a dict key, unwrapping an enum member to its value."""
    return key.value if isinstance(key, BrowserErrorCode) else str(key)


def _is_emittable(value: Any) -> bool:
    """Whether ``value`` is data a TypeScript ``const`` can hold.

    The filter is what lets :func:`_constant_names` discover constants instead of
    listing them: ``FRAME_SHAPES`` and ``FRAME_TYPEDDICTS`` are ``UPPER_CASE``
    exports too, and they hold *classes*, which become interfaces further down
    rather than values. Recursing means a container of classes is rejected as a
    whole rather than exploding inside :func:`_ts_value` — the same TypeError, but
    raised where the name is still known.
    """
    if isinstance(value, BrowserErrorCode):
        return True
    if isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (tuple, list)):
        return all(_is_emittable(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, (str, BrowserErrorCode)) and _is_emittable(item)
            for key, item in value.items()
        )
    return False


def _constant_names() -> list[str]:
    """The ``UPPER_CASE`` names in ``protocol.__all__``, in that file's order.

    Discovered rather than listed, so a constant added to the protocol reaches the
    extension without an edit to this generator. ``REMEDIES`` is skipped here and
    emitted with the error codes, where it belongs.
    """
    names: list[str] = []
    for name in P.__all__:
        if name == "REMEDIES" or not name.replace("_", "").isupper():
            continue
        if _is_emittable(getattr(P, name)):
            names.append(name)
    return names


def render() -> str:
    """The complete TypeScript module, as text with ``\\n`` line ends."""
    lines: list[str] = [HEADER.rstrip("\n"), ""]

    lines.append("// --- Constants, from protocol.py ---")
    lines.append("")
    for name in _constant_names():
        lines.append(f"export const {name} = {_ts_value(getattr(P, name))};")
    lines.append("")

    lines.append("// --- Error codes and their model-actionable remedies, from errors.py ---")
    lines.append("")
    union = "\n  | ".join(json.dumps(code.value) for code in BrowserErrorCode)
    lines.append(f"export type BrowserErrorCode =\n  | {union};")
    lines.append("")
    codes = ", ".join(json.dumps(code.value) for code in BrowserErrorCode)
    lines.append(f"export const BROWSER_ERROR_CODES = [{codes}] as const;")
    lines.append("")
    lines.append("export const REMEDIES: Record<BrowserErrorCode, string> = {")
    for code, text in REMEDIES.items():
        lines.append(f"  {json.dumps(code.value)}: {json.dumps(text)},")
    lines.append("};")
    lines.append("")

    lines.append("// --- Frame and payload shapes, from the TypedDicts in protocol.py ---")
    lines.append("")
    for shape in ALL_SHAPES:
        doc = (shape.__doc__ or "").strip().splitlines()
        summary = doc[0].strip() if doc else ""
        if summary:
            lines.append(f"/** {summary} */")
        lines.append(f"export interface {shape.__name__} {{")
        # `include_extras=True` keeps the NotRequired wrapper, and reading it is
        # the only reliable way to find the optional keys here: this module uses
        # `from __future__ import annotations`, so every annotation is a STRING at
        # class-creation time and TypedDict cannot classify it — `__optional_keys__`
        # comes back empty and every field would generate as required. That is the
        # silent version of the bug: `error` and `result` would be mandatory in the
        # TypeScript while the daemon sends exactly one of them, and the extension's
        # build would fail on a shape the wire never uses.
        hints = get_type_hints(shape, include_extras=True)
        for field, annotation in hints.items():
            optional = get_origin(annotation) is NotRequired
            inner = get_args(annotation)[0] if optional else annotation
            mark = "?" if optional else ""
            lines.append(f"  {field}{mark}: {_ts_type(inner)};")
        lines.append("}")
        lines.append("")

    lines.append("// --- Frame type -> shape, mirroring protocol.FRAME_SHAPES ---")
    lines.append("")
    lines.append("export type BrowserFrame =")
    # Frames only, and deliberately not `ALL_SHAPES`: a union that admitted
    # `SnapshotResult` would type-check a bare result object as a frame, and the
    # dispatcher's `frame.type` switch would then compile against a value that
    # has no `type` at all.
    for shape in P.FRAME_TYPEDDICTS[1:]:
        lines.append(f"  | {shape.__name__}")
    lines[-1] = lines[-1] + ";"
    lines.append("")

    return "\n".join(lines) + "\n"


def write(path: Path | None = None) -> Path:
    """Write the generated module, always with ``\\n`` line ends.

    ``newline="\\n"`` is explicit: on Windows Python would otherwise translate to
    ``\\r\\n`` and the committed file's bytes would depend on which machine ran
    the generator, so the drift test would pass locally and fail on CI (or the
    reverse). The reader normalises, and the writer never introduces the problem.
    """
    target = path or OUTPUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8", newline="\n")
    return target


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Prints the path written, so a developer can see it landed."""
    args = list(argv if argv is not None else sys.argv[1:])
    target = Path(args[0]) if args else None
    written = write(target)
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m`
    raise SystemExit(main())
