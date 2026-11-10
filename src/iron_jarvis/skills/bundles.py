"""Starter skill bundles (v1.464.0): skills that ship with the app and are
loaded only when the user chooses.

The owner's request: Iron Jarvis should come with starter skills a business can
choose to load — business, accounting, marketing, coding, project management,
social media — shown as bundles on the Templates page.

A bundle is a folder under ``skills/starter/<id>/`` holding ``bundle.toml``
(name, blurb, order) and one folder per skill with its ``SKILL.md``. NOTHING is
in the skill registry until the user loads it: loading copies the chosen skills
into ``<home>/skills/<slug>/SKILL.md``, where every other user skill lives, with
``created_by: starter`` and the bundle's id. From then on they are ordinary
skills: listed, searchable, editable, usable with "/".

THE RULES A LOAD KEEPS (each pinned in ``tests/test_skill_bundles_v1464.py``):

* A skill of the user's OWN with the same name is never overwritten. It is
  skipped and named.
* A starter skill the user EDITED after loading is never overwritten either,
  unless they ask (``replace``): the copy records the hash of what was loaded.
* Removing a bundle MOVES its unedited skills to ``<home>/trash/<stamp>/`` (the
  v1.256.0 rule: move, never delete). An edited one stays, and is named.
* Everything here is BLOCKING file work: routes call it off the event loop.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any

from .loader import SKILL_FILE, read_skill_md, slugify, write_skill_md

#: ``created_by`` of a loaded starter skill (Iron Jarvis's own words: trusted).
STARTER = "starter"
#: Frontmatter keys a loaded copy carries beside name and description.
BUNDLE_KEY = "bundle"
HASH_KEY = "bundle_sha"

ALREADY_YOURS = "you already have a skill with this name"
EDITED = "you changed it after loading it"
EDITED_KEPT = "you changed it, so it was kept"


def bundles_dir() -> Path:
    """Where the bundles ship (inside the package, like ``skills/builtin``)."""
    return Path(__file__).resolve().parent / "starter"


def body_hash(description: str, body: str) -> str:
    """What a loaded copy is compared by: the words a model reads."""
    text = (description or "").strip() + "\n\0\n" + (body or "").strip().replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _bundle_meta(folder: Path) -> dict[str, Any]:
    try:
        raw = tomllib.loads((folder / "bundle.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    name = str(raw.get("name") or folder.name).strip() or folder.name
    try:
        order = int(raw.get("order") or 999)
    except (TypeError, ValueError):
        order = 999
    return {"id": folder.name, "name": name, "blurb": str(raw.get("blurb") or "").strip(), "order": order}


def _shipped(folder: Path) -> list[dict[str, Any]]:
    """The skills one bundle ships: ``{name, description, body}``, by name."""
    out: list[dict[str, Any]] = []
    try:
        children = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return out
    for child in children:
        try:
            meta, body = read_skill_md(child)
        except (OSError, ValueError):
            continue
        name = str(meta.get("name") or child.name).strip() or child.name
        if not body.strip():
            continue
        out.append({"name": name, "description": str(meta.get("description") or "").strip(), "body": body})
    return out


def _installed(home: Path, name: str) -> "tuple[str, dict[str, Any], str] | None":
    """``(state, frontmatter, body)`` of the copy in the user's skills folder,
    or None when there is none. state: "own" (not from a bundle) | "loaded" |
    "edited"."""
    folder = Path(home) / "skills" / slugify(name)
    if not (folder / SKILL_FILE).is_file():
        return None
    try:
        meta, body = read_skill_md(folder)
    except (OSError, ValueError):
        return ("own", {}, "")
    if str(meta.get("created_by") or "").strip().lower() != STARTER:
        return ("own", meta, body)
    same = str(meta.get(HASH_KEY) or "") == body_hash(str(meta.get("description") or ""), body)
    return ("loaded" if same else "edited", meta, body)


def list_bundles(home: Path) -> list[dict[str, Any]]:
    """Every bundle with its skills and what is already loaded. Never raises."""
    out: list[dict[str, Any]] = []
    try:
        folders = [p for p in bundles_dir().iterdir() if p.is_dir() and (p / "bundle.toml").is_file()]
    except OSError:
        return out
    for folder in folders:
        info = _bundle_meta(folder)
        skills = []
        for sk in _shipped(folder):
            have = _installed(home, sk["name"])
            state = "not_loaded"
            if have is not None:
                state, meta, _ = have
                if state != "own" and str(meta.get(BUNDLE_KEY) or "") != info["id"]:
                    state = "own"  # loaded from another bundle of the same name
                if state == "loaded" and str(meta.get(HASH_KEY) or "") != body_hash(sk["description"], sk["body"]):
                    state = "update"  # unedited, and a newer copy ships now
            skills.append({"name": sk["name"], "description": sk["description"], "state": state})
        info["skills"] = skills
        info["loaded"] = sum(1 for s in skills if s["state"] in ("loaded", "edited", "update"))
        out.append(info)
    out.sort(key=lambda b: (b["order"], b["name"].lower()))
    return out


class UnknownBundle(LookupError):
    """No bundle of that id ships."""


def _folder_for(bundle_id: str) -> Path:
    wanted = str(bundle_id or "").strip()
    # The id is compared with the folders that ship, never joined into a path.
    try:
        for p in bundles_dir().iterdir():
            if p.is_dir() and p.name == wanted and (p / "bundle.toml").is_file():
                return p
    except OSError:
        pass
    raise UnknownBundle(f"There is no starter bundle called “{wanted}”.")


def load_bundle(
    home: Path, bundle_id: str, names: "list[str] | None" = None, *, replace: bool = False
) -> dict[str, Any]:
    """Copy the chosen skills of one bundle into the user's skills folder.

    ``names`` None = every skill of the bundle. Returns ``{loaded, skipped}``;
    a skipped entry says why in plain words. An unknown name is skipped as
    "not in this bundle" (nothing is loaded for it)."""
    folder = _folder_for(bundle_id)
    shipped = {sk["name"]: sk for sk in _shipped(folder)}
    wanted = list(shipped) if names is None else [str(n).strip() for n in names if str(n).strip()]
    loaded: list[str] = []
    skipped: list[dict[str, str]] = []
    for name in dict.fromkeys(wanted):
        sk = shipped.get(name)
        if sk is None:
            skipped.append({"name": name, "reason": "it is not in this bundle"})
            continue
        have = _installed(home, name)
        if have is not None:
            state, meta, _ = have
            if state == "own" or str(meta.get(BUNDLE_KEY) or "") != folder.name:
                skipped.append({"name": name, "reason": ALREADY_YOURS})
                continue
            if state == "edited" and not replace:
                skipped.append({"name": name, "reason": EDITED})
                continue
        write_skill_md(
            Path(home) / "skills" / slugify(name),
            {
                "name": name,
                "description": sk["description"],
                "created_by": STARTER,
                BUNDLE_KEY: folder.name,
                HASH_KEY: body_hash(sk["description"], sk["body"]),
            },
            sk["body"],
        )
        loaded.append(name)
    return {"bundle": folder.name, "loaded": loaded, "skipped": skipped}


def remove_bundle(home: Path, bundle_id: str) -> dict[str, Any]:
    """Take one bundle's loaded skills back out: MOVED to the trash folder,
    never deleted. A skill the user edited is kept and named."""
    folder = _folder_for(bundle_id)
    removed: list[str] = []
    kept: list[dict[str, str]] = []
    trash = Path(home) / "trash" / time.strftime("%Y%m%d-%H%M%S") / "starter-skills" / folder.name
    for sk in _shipped(folder):
        have = _installed(home, sk["name"])
        if have is None:
            continue
        state, meta, _ = have
        if state == "own" or str(meta.get(BUNDLE_KEY) or "") != folder.name:
            continue
        if state == "edited":
            kept.append({"name": sk["name"], "reason": EDITED_KEPT})
            continue
        src = Path(home) / "skills" / slugify(sk["name"])
        try:
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(trash / src.name))
            removed.append(sk["name"])
        except OSError as exc:
            kept.append({"name": sk["name"], "reason": f"it could not be moved ({exc.__class__.__name__})"})
    return {"bundle": folder.name, "removed": removed, "kept": kept,
            "trash": str(trash) if removed else ""}
