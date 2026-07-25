"""Skill Registry (§23).

Discovers ``<dir>/<name>/SKILL.md`` bundles, exposes search/list/get, and can
inject named skills' instructions into an agent's system prompt (§11/§23). Skill
search is intentionally simple: case-insensitive token overlap of the query
against each skill's name + description.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .loader import SKILL_FILE, Skill, load_skill


def builtin_dir() -> Path:
    """Path to the bundled example skills shipped with Iron Jarvis (§23)."""
    return Path(__file__).resolve().parent / "builtin"


def external_skill_roots() -> list[tuple[Path, str]]:
    """Well-known external skill locations to pull in, each with a source tag.

    Scanned RECURSIVELY (see ``discover_recursive``) so nested layouts are all
    found — Claude Code skills (``~/.claude/skills/<name>/SKILL.md``), Claude
    plugin skills (``~/.claude/plugins/**/SKILL.md``), and Codex skills
    (``~/.codex/skills/**/SKILL.md``, including its ``.system/`` bundles). Only
    existing directories are returned.
    """
    home = Path.home()
    candidates = [
        (home / ".claude" / "skills", "claude"),
        (home / ".claude" / "plugins", "claude"),
        (home / ".codex" / "skills", "codex"),
    ]
    return [(p, tag) for p, tag in candidates if p.is_dir()]


def marketplace_catalog_dirs(home: "Path | None" = None) -> list[Path]:
    """Cloned plugin-MARKETPLACE checkouts, which must NOT be read as installed
    skills (v1.98.0).

    Adding a marketplace clones its whole catalog into
    ``~/.claude/plugins/marketplaces/<name>/`` so it can be BROWSED — sample
    plugins, authoring kits, and every listed plugin's payload. A plain
    ``**/SKILL.md`` glob can't tell "available in a store" from "installed by
    the user", so it swallowed the lot: on this machine that was 27 skills
    (``plugin-dev``, ``mcp-server-dev``, discord/imessage/telegram, and one
    literally named ``example-plugin``) against 13 real ones — skills the user
    never installed, searchable by agents and injected into prompts.

    Catalogs are identified from ``known_marketplaces.json`` (the file Claude
    Code actually maintains) so a marketplace installed anywhere is caught, with
    the conventional ``plugins/marketplaces/*`` layout as the fallback for a
    missing or unreadable manifest. Genuinely installed plugins living ELSEWHERE
    under ``plugins/`` keep loading exactly as before.
    """
    root = (home or Path.home()) / ".claude" / "plugins"
    if not root.is_dir():
        return []
    found: list[Path] = []
    manifest = root / "known_marketplaces.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in (data or {}).values():
            loc = str((entry or {}).get("installLocation") or "").strip()
            if loc:
                found.append(Path(loc))
    except Exception:  # noqa: BLE001 — absent/corrupt manifest -> use the layout
        pass
    # Fallback + belt-and-braces: the conventional location, whether or not the
    # manifest listed it. A catalog missed here would silently re-pollute.
    conventional = root / "marketplaces"
    if conventional.is_dir():
        found.extend(p for p in conventional.iterdir() if p.is_dir())
        found.append(conventional)
    out: list[Path] = []
    for p in found:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp not in out:
            out.append(rp)
    return out


def _under_any(path: Path, roots: list[Path]) -> bool:
    """True when ``path`` lives inside any of ``roots`` (resolved, so a symlinked
    or differently-cased marketplace clone is still recognised)."""
    try:
        rp = path.resolve()
    except OSError:
        rp = path
    for root in roots:
        try:
            if rp == root or rp.is_relative_to(root):
                return True
        except (OSError, ValueError):
            continue
    return False


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens used for relevance ranking."""
    return re.findall(r"[a-z0-9]+", text.lower())


class SkillRegistry:
    """In-memory registry of discovered skills (§23)."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    # Allow both ``SkillRegistry.builtin_dir()`` and ``framework.builtin_dir()``.
    builtin_dir = staticmethod(builtin_dir)

    def discover(self, *dirs: Path, source: str = "user") -> "SkillRegistry":
        """Load every ``<dir>/<name>/SKILL.md`` (one level) found under each ``dir``.

        Last-wins on name collision. Missing directories are skipped so callers
        can pass an as-yet-uncreated ``config.home/'skills'`` safely. Returns
        self for chaining.
        """
        for d in dirs:
            base = Path(d)
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / SKILL_FILE).is_file():
                    try:
                        skill = load_skill(child, source=source)
                    except Exception:  # a malformed SKILL.md shouldn't kill discovery
                        continue
                    self._skills[skill.name] = skill
        return self

    def discover_recursive(
        self,
        *dirs: Path,
        source: str = "custom",
        max_files: int = 2000,
        exclude: "list[Path] | None" = None,
    ) -> "SkillRegistry":
        """Load EVERY ``<root>/**/SKILL.md`` (any depth) under each root.

        This is what picks up Claude/Codex/plugin skills laid out in nested
        folders. FIRST-wins on name collision, so already-registered builtin/user
        skills are never clobbered by an external one of the same name. Bounded
        (``max_files`` per root) and fault-tolerant (a bad SKILL.md is skipped).

        ``exclude`` drops any SKILL.md living under one of those directories —
        used to skip cloned marketplace CATALOGS, which are a store to browse
        rather than skills the user installed (see
        :func:`marketplace_catalog_dirs`).
        """
        skips = [Path(p) for p in (exclude or [])]
        for d in dirs:
            base = Path(d)
            if not base.is_dir():
                continue
            try:
                files = sorted(base.rglob(SKILL_FILE))
            except OSError:
                continue
            if skips:
                files = [f for f in files if not _under_any(f, skips)]
            for md in files[:max_files]:
                try:
                    skill = load_skill(md.parent, source=source)
                except Exception:
                    continue
                if skill.name and skill.name not in self._skills:
                    self._skills[skill.name] = skill
        return self

    def repopulate(
        self, home: Path, extra_paths: list[str] | None = None
    ) -> "SkillRegistry":
        """Rebuild the WHOLE registry IN PLACE from every source, in precedence
        order: builtin < user < external (Claude/Codex) < custom paths, with
        builtin/user winning over external of the same name.

        In-place (clears + refills ``self._skills``) so the two skill tools bound
        to this registry keep seeing the current set — used at boot and on rescan.
        """
        self._skills.clear()
        self.discover(builtin_dir(), source="builtin")
        self.discover(Path(home) / "skills", source="user")
        # A cloned marketplace is a STORE to browse, not skills the user
        # installed — excluded so the catalog's sample/authoring plugins never
        # enter the registry (v1.98.0).
        catalogs = marketplace_catalog_dirs()
        for root, tag in external_skill_roots():
            self.discover_recursive(root, source=tag, exclude=catalogs)
        for extra in extra_paths or []:
            try:
                self.discover_recursive(Path(extra).expanduser(), source="custom")
            except (OSError, ValueError):
                continue
        return self

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        return [self._skills[n] for n in sorted(self._skills)]

    def search(self, query: str, k: int = 5) -> list[Skill]:
        """Rank skills by token overlap of ``query`` vs name + description."""
        terms = set(_tokens(query))
        if not terms:
            return []
        scored: list[tuple[int, Skill]] = []
        for skill in self._skills.values():
            hay = set(_tokens(f"{skill.name} {skill.description}"))
            score = len(terms & hay)
            if score:
                scored.append((score, skill))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [skill for _, skill in scored[:k]]

    def inject(self, system_prompt: str, skill_names: list[str]) -> str:
        """Append a ``# Skills`` section with each named skill's instructions."""
        blocks: list[str] = []
        for name in skill_names:
            skill = self._skills.get(name)
            if skill is None:
                continue
            blocks.append(f"## {skill.name}\n{skill.instructions}")
        if not blocks:
            return system_prompt
        return system_prompt + "\n\n# Skills\n" + "\n\n".join(blocks)
