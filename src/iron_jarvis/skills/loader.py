"""Skill loading (§23).

Parses a ``SKILL.md`` file with YAML frontmatter into a :class:`Skill`. The
frontmatter carries ``name`` / ``description``; the markdown body becomes the
skill's ``instructions``. Optional ``examples/``, ``scripts/`` and ``templates/``
subfolders are discovered as filename lists.

Provenance (v1.297.0, the skill curator): the frontmatter may also carry
``created_by`` ("user" | "agent" | "proposal"), ``created_session`` (the
session that wrote it, or ""), ``pinned`` (bool) and ``archived_at`` (ISO
timestamp, present only on an archived copy). A SKILL.md without them is a
user-authored, unpinned skill — every pre-wave file reads exactly as before.
Any OTHER key in the frontmatter is preserved on rewrite: the writers here
round-trip the whole frontmatter dict instead of rebuilding it from the two
fields they know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SKILL_FILE = "SKILL.md"

#: Who wrote a skill. "user" is the default and the only value a plain
#: SKILL.md (no provenance keys) ever reads as.
CREATED_BY_VALUES = ("user", "agent", "proposal", "recording")


def slugify(name: str) -> str:
    """A safe directory slug for a skill name (letters/digits/dash)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "skill"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``--- yaml --- body`` into (metadata, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, parts[2].lstrip("\n")
    return {}, text


def compose_skill_md(meta: dict[str, Any], body: str) -> str:
    """Canonical SKILL.md text: the WHOLE ``meta`` dict as frontmatter (keys in
    insertion order, unknown keys included) + the body."""
    # yaml.safe_dump escapes special chars, so a name with ':' or quotes is safe.
    front = yaml.safe_dump(dict(meta), sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{(body or '').strip()}\n"


def read_skill_md(skill_dir: Path) -> tuple[dict[str, Any], str]:
    """``(frontmatter dict, body)`` of ``<skill_dir>/SKILL.md``.

    Raises ``FileNotFoundError`` when there is no SKILL.md.
    """
    md = Path(skill_dir) / SKILL_FILE
    if not md.is_file():
        raise FileNotFoundError(f"no {SKILL_FILE} found in skill dir: {skill_dir}")
    return _parse_frontmatter(md.read_text(encoding="utf-8"))


def write_skill_md(skill_dir: Path, meta: dict[str, Any], body: str) -> Path:
    """Write ``<skill_dir>/SKILL.md`` from a frontmatter dict + body."""
    skill_dir = Path(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / SKILL_FILE
    md.write_text(compose_skill_md(meta, body), encoding="utf-8")
    return md


def update_frontmatter(skill_dir: Path, **changes: Any) -> dict[str, Any]:
    """Rewrite ONLY the frontmatter of an existing SKILL.md.

    Every existing key (known or not) survives; a change whose value is
    ``None`` REMOVES that key. The body is written back untouched. Returns the
    frontmatter as written.
    """
    meta, body = read_skill_md(skill_dir)
    for key, value in changes.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    write_skill_md(skill_dir, meta, body)
    return meta


def save_skill(
    skills_root: Path,
    name: str,
    description: str,
    instructions: str,
    *,
    created_by: str | None = None,
    created_session: str | None = None,
    pinned: bool | None = None,
) -> Path:
    """Write ``<skills_root>/<slug>/SKILL.md`` and return its dir.

    The frontmatter carries the display name + description; the body is the
    instructions. Overwrites an existing skill of the same slug (edit in
    place) — and when it does, every frontmatter key the file already had
    (provenance, pin, anything unknown) is carried over, so an edit never
    strips who wrote a skill or un-pins it.

    Provenance kwargs (v1.297.0): a value given here WINS; ``None`` keeps the
    file's existing value, and a brand-new file with ``None`` is a plain
    user-authored, unpinned skill (the keys are not written at all, so a
    user's own skill files look exactly as they always did).
    Raises ``ValueError`` on an empty name or empty instructions.
    """
    name = (name or "").strip()
    instructions = (instructions or "").strip()
    if not name:
        raise ValueError("skill name is required")
    if not instructions:
        raise ValueError("skill instructions are required")
    if created_by is not None and created_by not in CREATED_BY_VALUES:
        raise ValueError(f"created_by must be one of {CREATED_BY_VALUES}")
    skill_dir = Path(skills_root) / slugify(name)
    existing: dict[str, Any] = {}
    if (skill_dir / SKILL_FILE).is_file():
        try:
            existing, _ = read_skill_md(skill_dir)
        except Exception:  # noqa: BLE001 — a mangled file is simply overwritten
            existing = {}
    meta: dict[str, Any] = {"name": name, "description": (description or "").strip()}
    for key, value in existing.items():
        if key not in meta:
            meta[key] = value
    if created_by is not None:
        meta["created_by"] = created_by
    if created_session is not None:
        meta["created_session"] = str(created_session)
    if pinned is not None:
        meta["pinned"] = bool(pinned)
    write_skill_md(skill_dir, meta, instructions)
    return skill_dir


@dataclass
class Skill:
    """A reusable instruction bundle (§23)."""

    name: str
    description: str
    instructions: str
    dir: Path
    examples: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)
    #: Where this skill came from — "builtin", "user", "claude", "codex", or
    #: "custom" (a user-added search path). Surfaced in the dashboard so it's
    #: clear which skills are Iron Jarvis's own vs pulled in from Claude/Codex.
    source: str = "user"
    #: Provenance (v1.297.0): who wrote the file — "user" (the default, and
    #: what any SKILL.md without the key reads as), "agent" (``skill_create``
    #: from a run) or "proposal" (an approved learning-engine draft).
    created_by: str = "user"
    #: The session that wrote it, or "" when nobody recorded one.
    created_session: str = ""
    #: A pinned skill is exempt from the curator's automatic sweep.
    pinned: bool = False
    #: ISO timestamp stamped when the curator archived this copy; "" live.
    archived_at: str = ""
    #: v1.417.0 (skill parity): the frontmatter the harnesses a skill came
    #: from honour (Claude Code 2.1.316, Codex 0.157.0, Grok, Pi, OpenCode —
    #: the Agent Skills fields). ``when_to_use`` (``when_to_use`` /
    #: ``when-to-use``) is added to the listing's description as Claude Code
    #: does ("<description> - <when to use>").
    when_to_use: str = ""
    #: False when ``disable-model-invocation: true`` (Claude Code, Grok, Pi)
    #: or Codex's ``agents/openai.yaml`` says ``policy:
    #: allow_implicit_invocation: false``: the skill is not listed to the
    #: model, not found by ``skill_search`` and ``skill_load`` refuses it —
    #: only the user's "/name" runs it.
    model_invocable: bool = True
    #: False when ``user-invocable: false``: not offered in the "/" menu (the
    #: model may still load it).
    user_invocable: bool = True
    #: ``arguments`` — the NAMED ``$name`` placeholders (Claude Code).
    argument_names: tuple[str, ...] = ()
    #: ``argument-hint`` — the "/" menu's hint.
    argument_hint: str = ""
    #: ``allowed-tools`` as written. READ, NEVER OBEYED: Iron Jarvis's own
    #: approval rules decide every call (a skill never grants a tool).
    allowed_tools: tuple[str, ...] = ()


def _list_files(directory: Path) -> list[str]:
    """Return sorted filenames directly inside ``directory`` (empty if absent)."""
    if directory.is_dir():
        return sorted(p.name for p in directory.iterdir() if p.is_file())
    return []


def _created_by(meta: dict) -> str:
    value = str(meta.get("created_by") or "").strip().lower()
    return value if value in CREATED_BY_VALUES else "user"


def _flag(value: Any, default: bool) -> bool:
    """A frontmatter boolean the way Claude Code reads one: true/false,
    yes/no, on/off, 1/0; anything else is ``default``."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    return default


def _named_args(value: Any) -> tuple[str, ...]:
    """``arguments: [issue, branch]`` (or a space-separated string)."""
    if isinstance(value, str):
        items: list[Any] = value.split()
    elif isinstance(value, list):
        items = value
    else:
        return ()
    out: list[str] = []
    for item in items:
        name = str(item or "").strip()
        if name and not name.isdigit() and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name) and name not in out:
            out.append(name)
    return tuple(out[:64])


def _hint(value: Any) -> str:
    """``argument-hint`` as Claude Code shows it: YAML reads ``[file]`` as a
    list, which JavaScript's ``String`` writes as ``file`` (items joined by a
    comma)."""
    if isinstance(value, list):
        value = ",".join(str(v) for v in value)
    return " ".join(str(value or "").split())[:200]


def _tool_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items: list[Any] = re.split(r"[,\s]+", value)
    elif isinstance(value, list):
        items = value
    else:
        return ()
    return tuple(str(i).strip() for i in items if str(i or "").strip())[:64]


#: Codex's per-skill policy file (``<skill>/agents/openai.yaml``) is small.
_OPENAI_YAML_BYTES = 64 * 1024


def _codex_allows_implicit(skill_dir: Path) -> bool:
    """Codex 0.157.0: ``agents/openai.yaml`` ``policy.allow_implicit_invocation:
    false`` keeps a skill out of the model's list (measured with ``codex debug
    prompt-input``: ``review-agent`` is not listed). Absent/unreadable = True."""
    path = Path(skill_dir) / "agents" / "openai.yaml"
    try:
        if not path.is_file() or path.stat().st_size > _OPENAI_YAML_BYTES:
            return True
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a bad policy file never hides a skill
        return True
    policy = data.get("policy") if isinstance(data, dict) else None
    if isinstance(policy, dict) and "allow_implicit_invocation" in policy:
        return _flag(policy.get("allow_implicit_invocation"), True)
    return True


def load_skill(dir: Path, source: str = "user") -> Skill:
    """Load ``dir/SKILL.md`` into a :class:`Skill` (§23).

    ``source`` tags where the skill came from (builtin/user/claude/codex/custom).
    Raises ``FileNotFoundError`` with a clear message if SKILL.md is missing.
    """
    skill_dir = Path(dir)
    meta, body = read_skill_md(skill_dir)
    name = str(meta.get("name") or skill_dir.name).strip()
    description = str(meta.get("description") or "").strip()

    return Skill(
        name=name,
        description=description,
        instructions=body.strip(),
        dir=skill_dir,
        examples=_list_files(skill_dir / "examples"),
        scripts=_list_files(skill_dir / "scripts"),
        templates=_list_files(skill_dir / "templates"),
        source=source,
        created_by=_created_by(meta),
        created_session=str(meta.get("created_session") or "").strip(),
        pinned=bool(meta.get("pinned", False)),
        archived_at=str(meta.get("archived_at") or "").strip(),
        when_to_use=" ".join(str(meta.get("when_to_use") or meta.get("when-to-use") or "").split()),
        model_invocable=(
            not _flag(meta.get("disable-model-invocation"), False)
            and _codex_allows_implicit(skill_dir)
        ),
        user_invocable=_flag(meta.get("user-invocable", True), True),
        argument_names=_named_args(meta.get("arguments")),
        argument_hint=_hint(meta.get("argument-hint")),
        allowed_tools=_tool_list(meta.get("allowed-tools")),
    )
