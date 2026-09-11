"""Fill in PDF forms (C-09).

"Fill this W-9 from the client file" used to produce a text or Word version —
never the government form itself. These functions read a PDF's own form fields
(AcroForm) and fill them into a COPY with pypdf, then RE-OPEN the copy and check
every value actually took, the same discipline redaction uses: a copy whose
fields did not take is deleted and the call fails, so "filled" is never claimed
over a form that still shows blanks.

A PDF with no form fields is a flat page. That is said plainly; nothing is ever
typed on top of the page image.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_TYPES = {"/Tx": "text", "/Ch": "choice", "/Sig": "signature"}
_TRUE = {"1", "true", "yes", "y", "x", "on", "checked", "check", "tick", "ticked"}
_FALSE = {"", "0", "false", "no", "n", "off", "unchecked", "none"}


class FormError(ValueError):
    """A form that could not be read or filled; the message names why."""


def _open(path: Path):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")
        except Exception:  # noqa: BLE001
            ok = 0
        if not ok:
            raise FormError(f"{path.name} is password-protected — it cannot be read without the password")
    return reader


def _describe(reader) -> list[dict[str, Any]]:
    fields = reader.get_fields() or {}
    out: list[dict[str, Any]] = []
    for name, f in fields.items():
        ft = f.get("/FT")
        if ft is None:
            continue  # a container node, not a field you can fill
        flags = int(f.get("/Ff", 0) or 0)
        if ft == "/Btn":
            kind = "radio" if flags & (1 << 15) else ("button" if flags & (1 << 16) else "checkbox")
        else:
            kind = _TYPES.get(str(ft), str(ft).lstrip("/").lower())
        value = f.get("/V")
        entry: dict[str, Any] = {"name": str(name), "type": kind, "value": "" if value is None else str(value)}
        states = [str(s) for s in (f.get("/_States_") or [])]
        if kind in ("checkbox", "radio") and states:
            entry["options"] = states
        opts = f.get("/Opt")
        if kind == "choice" and opts:
            shown: list[str] = []
            for o in opts:
                try:
                    shown.append(str(o[0]) if isinstance(o, (list, tuple)) else str(o))
                except Exception:  # noqa: BLE001
                    continue
            entry["options"] = shown
        out.append(entry)
    return out


def list_fields(path: Path) -> list[dict[str, Any]]:
    """Every fillable field: ``name``, ``type``, current ``value`` and, where the
    form defines them, the allowed ``options``."""
    return _describe(_open(path))


def _resolve_names(fields: dict[str, dict[str, Any]], values: dict[str, Any]) -> dict[str, Any]:
    """Map each requested key to a real field name — exact first, then a unique
    last-segment match ("f1_01[0]" for "topmostSubform[0].Page1[0].f1_01[0]")."""
    out: dict[str, Any] = {}
    unknown: list[str] = []
    for key, value in values.items():
        k = str(key)
        if k in fields:
            out[k] = value
            continue
        hits = [n for n in fields if n.split(".")[-1] == k or n.endswith("." + k)]
        if len(hits) == 1:
            out[hits[0]] = value
        else:
            unknown.append(k)
    if unknown:
        sample = ", ".join(list(fields)[:25])
        more = f" (and {len(fields) - 25} more)" if len(fields) > 25 else ""
        raise FormError(
            "no form field named " + ", ".join(f"\"{u}\"" for u in unknown)
            + f". This form's fields are: {sample}{more}"
        )
    return out


def _on_state(field: dict[str, Any], value: Any) -> str:
    states = field.get("options") or []
    on = [s for s in states if s != "/Off"]
    raw = value
    if isinstance(raw, bool):
        want_on = raw
    else:
        text = str(raw).strip()
        low = text.lower()
        named = [s for s in states if s.lstrip("/").lower() == low.lstrip("/")]
        if named:
            return named[0]
        if low in _TRUE:
            want_on = True
        elif low in _FALSE:
            want_on = False
        else:
            raise FormError(
                f"\"{field['name']}\" is a {field['type']}: use one of "
                + ", ".join(states or ["/Off", "/Yes"])
                + f", or yes/no — not \"{text}\""
            )
    if not want_on:
        return "/Off"
    if field["type"] == "radio":
        raise FormError(
            f"\"{field['name']}\" is a set of options: say which one ({', '.join(on)})"
        )
    return on[0] if on else "/Yes"


def _same(a: Any, b: Any) -> bool:
    return str(a or "").strip().lstrip("/").casefold() == str(b or "").strip().lstrip("/").casefold()


def fill_form(src: Path, dst: Path, values: dict[str, Any]) -> dict[str, Any]:
    """Fill *values* into a copy of *src* at *dst* and verify it.

    Returns ``{filled, values, still_empty}``. Raises :class:`FormError` — with
    no file left behind — when a field is unknown, a value is invalid, or the
    written copy does not show a requested value."""
    from pypdf import PdfWriter

    if not values:
        raise FormError("nothing to fill — pass at least one field and its value")
    reader = _open(src)
    described = _describe(reader)
    if not described:
        raise FormError(
            f"{src.name} has no fillable form fields — it is a flat PDF, so there "
            "is nothing to fill in. (Iron Jarvis does not type text on top of a "
            "page; fill it in a PDF editor, or ask for the answers as a list.)"
        )
    fields = {f["name"]: f for f in described}
    resolved_raw = _resolve_names(fields, dict(values))
    resolved: dict[str, str] = {}
    for name, value in resolved_raw.items():
        f = fields[name]
        if f["type"] in ("checkbox", "radio"):
            resolved[name] = _on_state(f, value)
        elif f["type"] in ("signature", "button"):
            raise FormError(f"\"{name}\" is a {f['type']} field and cannot be filled in")
        else:
            text = "" if value is None else str(value)
            opts = f.get("options") or []
            if f["type"] == "choice" and opts and text not in opts:
                raise FormError(
                    f"\"{name}\" only accepts one of: {', '.join(opts)} — not \"{text}\""
                )
            resolved[name] = text

    writer = PdfWriter(clone_from=reader)
    # A FORM pypdf CANNOT WRITE IS STILL ANSWERED IN WORDS. Measured: a widget
    # carrying no appearance dictionary makes `update_page_form_field_values`
    # raise a bare `KeyError: '/AP'` from inside the library — a traceback that
    # names nothing the user or the model can act on, over a file we had already
    # said had fillable fields. Every other refusal in this function is a
    # sentence; so is this one.
    try:
        writer.update_page_form_field_values(None, resolved, auto_regenerate=False)
        writer.set_need_appearances_writer(True)
    except FormError:
        raise
    except Exception as exc:  # noqa: BLE001 — any library failure, said plainly
        raise FormError(
            f"{src.name} lists fillable fields, but this app could not write to "
            f"them ({type(exc).__name__}: {exc}) — the form is damaged or built "
            "in a way Iron Jarvis cannot fill. Nothing was written."
        ) from exc
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    # RE-OPEN AND CHECK — the only thing that makes "filled" true.
    after = {f["name"]: f for f in list_fields(dst)}
    missed = [n for n, want in resolved.items() if not _same(after.get(n, {}).get("value"), want)]
    if missed:
        try:
            dst.unlink()
        except OSError:
            pass
        raise FormError(
            "these fields did not take in the written copy: "
            + ", ".join(missed)
            + " — no file was kept"
        )
    still_empty = [
        n for n, f in after.items()
        if f["type"] == "text" and not str(f.get("value") or "").strip()
    ]
    return {"filled": len(resolved), "values": resolved, "still_empty": still_empty}
