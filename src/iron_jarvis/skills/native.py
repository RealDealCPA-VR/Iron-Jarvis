"""Skills that work the way the harness they came from runs them (v1.417.0).

THE USER: "I want these skills that are native across different harnesses to
work exactly the same way inside our native harness." Measured on this PC
(each harness's own binary, docs and a no-model probe), not recalled:

* CLAUDE CODE 2.1.316 — reads ``<CLAUDE_CONFIG_DIR or ~/.claude>/skills/
  <name>/SKILL.md`` (one level), the signed-in account's claude.ai synced
  skills (``skills/synced/<org>_<account>/<name>``), the skills of every
  plugin it LOADS (installed or synced, ``enabledPlugins`` not off — never a
  marketplace catalog, never ``.trash``) and, for a session, the project's
  ``.claude/skills`` from its folder up through the repository root. It
  LISTS every model-invocable skill up front ("The following skills are
  available for use with the Skill tool:" + ``- name: description - when to
  use``, within 1% of the window and 1,536 characters a description). Its
  ``Skill`` tool (and the user's ``/name words``) hands the model ``Base
  directory for this skill: <dir>`` + the body with ``$ARGUMENTS`` / ``$N``
  (0-based) / named ``$x`` / ``${CLAUDE_SKILL_DIR}`` filled.
  ``disable-model-invocation: true`` keeps a skill out of the list and the
  model's reach; ``user-invocable: false`` keeps it out of "/".
* CODEX 0.157.0 — ``$CODEX_HOME/skills`` (with ``.system``),
  ``~/.agents/skills`` and the project's ``.agents/skills`` from the folder up
  to the repository root (``codex debug prompt-input``). Lists ``- name:
  description (file: …/SKILL.md)`` and the model reads the file, resolving
  relative paths against its folder. ``agents/openai.yaml`` ``policy:
  allow_implicit_invocation: false`` keeps one out of the list.
* GROK 1.0.5 — ``~/.grok/skills``, ``.grok/skills``, ``.agents/skills``,
  ``.claude/skills`` (cwd to repo root), and ``~/.claude/skills``.
* PI 1.3.0 — ``~/.pi/agent/skills``, ``.pi/skills``, ``~/.agents/skills``,
  ``.agents/skills`` (cwd up to the repo root).
* OPENCODE 1.18 — ``~/.config/opencode/{skill,skills}``, ``.opencode/…``,
  ``.claude/skills`` and ``.agents/skills``; its ``skill`` tool also returns
  ``Base directory for this skill: <dir>``.

This module is the ONE place for those rules: :func:`user_roots` (what the
registry loads), :func:`project_skills` (a session folder's own), the
listing (:func:`listing`) and what invoking one hands the model
(:func:`invocation_text`). Permissions stay Iron Jarvis's: ``allowed-tools``
is read and never obeyed, and a skill that did not come from the user is
scanned before a model reads it (``framework.guarded_instructions``).
Nothing here writes a harness's files. BLOCKING (stats + small reads):
callers run it off the loop.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable

from .loader import SKILL_FILE, Skill, load_skill

log = logging.getLogger(__name__)

#: How a root is read: ``"one"`` — ``<root>/<name>/SKILL.md`` only (Claude
#: Code's skills folder, Grok's); ``"tree"`` — every ``SKILL.md`` below it;
#: ``"skill"`` — the root IS one skill's folder (a plugin's skill).
ONE, TREE, SKILL = "one", "tree", "skill"

#: Where a session folder's own skills live, per harness (nearest first is
#: the folder walk's job). Each is read one level deep except the ones a
#: harness walks recursively (Pi, OpenCode).
PROJECT_SKILL_DIRS: tuple[tuple[tuple[str, ...], str], ...] = (
    ((".claude", "skills"), ONE),     # Claude Code, Grok, OpenCode
    ((".agents", "skills"), TREE),    # Codex, Pi, Grok, OpenCode
    ((".grok", "skills"), ONE),       # Grok
    ((".opencode", "skills"), TREE),  # OpenCode
    ((".opencode", "skill"), TREE),   # OpenCode (older name)
    ((".pi", "skills"), TREE),        # Pi
)

#: A session folder walk stops after this many folders (a deep folder with no
#: repository above it reads only itself — see :func:`_walk_up`).
MAX_WALK = 12
#: Skill files read under one project root, at most.
MAX_PROJECT_FILES = 200
#: Claude Code's listing: a description is cut at 1,536 characters, and the
#: whole list gets 1% of the window (4 characters a token).
LISTING_DESC_CHARS = 1536
LISTING_FRACTION = 0.01
#: What the listing assumes when the window is not known (Claude Code: 200K).
LISTING_DEFAULT_WINDOW = 200_000
#: A user's "/name" hands the model the whole body up to this (Grok's own
#: cap is 25,000 tokens; a real skill on this PC is 37 KB — the chat's old
#: 8,000-character playbook cut it at a fifth).
INVOKE_CHARS = 100_000
#: The project skills' source tag (scanned: not the user's own app skills).
SOURCE_PROJECT = "project"


# --------------------------------------------------------------------------- #
# where the user's skills live
# --------------------------------------------------------------------------- #


def codex_home() -> Path:
    """``CODEX_HOME`` when set, else ``~/.codex`` (Codex's own rule)."""
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def _claude_synced_dirs(base: Path) -> list[Path]:
    """``skills/synced/<bucket>`` folders Claude Code reads: the SIGNED-IN
    account's only (``claude_commands.signed_in_bucket`` — the rule its
    synced PLUGINS follow), every bucket when no account can be read."""
    synced = base / "skills" / "synced"
    try:
        buckets = sorted(p for p in synced.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    try:
        from ..harness.claude_commands import signed_in_bucket

        mine = signed_in_bucket()
    except Exception:  # noqa: BLE001
        mine = ""
    if mine:
        buckets = [b for b in buckets if b.name.lower() == mine]
    return buckets


def _claude_plugin_skill_dirs(folders: Iterable[str] = ()) -> list[Path]:
    """Every skill folder of every plugin Claude Code LOADS
    (``claude_commands.enabled_plugins``: installed or synced for the
    signed-in account, ``enabledPlugins`` not off; never a catalog or the
    trash)."""
    try:
        from ..harness import claude_commands as CC

        out: list[Path] = []
        for plugin in CC.enabled_plugins(list(folders)):
            out.extend(CC._plugin_skill_dirs(plugin))
        return out
    except Exception:  # noqa: BLE001 — a plugin file never breaks discovery
        log.warning("reading Claude Code's plugin skills failed", exc_info=True)
        return []


def user_roots() -> list[tuple[Path, str, str]]:
    """``(path, source tag, how)`` for every user-level skill place the
    harnesses on this PC read, in precedence order (first wins on a name).
    Only existing folders. ``how`` is :data:`ONE`, :data:`TREE` or
    :data:`SKILL`."""
    from .framework import claude_dir

    home = Path.home()
    claude = claude_dir()
    out: list[tuple[Path, str, str]] = [(claude / "skills", "claude", ONE)]
    out += [(b, "claude", ONE) for b in _claude_synced_dirs(claude)]
    out += [(d, "claude", SKILL) for d in _claude_plugin_skill_dirs()]
    out += [
        (codex_home() / "skills", "codex", TREE),
        (home / ".agents" / "skills", "agents", TREE),
        (home / ".grok" / "skills", "grok", ONE),
        (home / ".config" / "opencode" / "skills", "opencode", TREE),
        (home / ".config" / "opencode" / "skill", "opencode", TREE),
        (home / ".pi" / "agent" / "skills", "pi", TREE),
    ]
    found: list[tuple[Path, str, str]] = []
    for path, tag, how in out:
        try:
            ok = (path / SKILL_FILE).is_file() if how == SKILL else path.is_dir()
        except OSError:
            ok = False
        if ok:
            found.append((path, tag, how))
    return found


# --------------------------------------------------------------------------- #
# a session folder's own skills
# --------------------------------------------------------------------------- #


def _walk_up(folder: Path) -> list[Path]:
    """The folder and its parents up to (and including) the repository root
    — the first folder holding ``.git`` — nearest first. No repository above
    it: the folder alone (Codex and Pi stop at the repository root; with
    none, the working folder is the root). Never the user's home or above."""
    try:
        start = folder.resolve()
    except OSError:
        return []
    try:
        home = Path.home().resolve()
    except OSError:
        home = None
    chain: list[Path] = []
    cur = start
    for _ in range(MAX_WALK):
        if home is not None and cur == home:
            break
        chain.append(cur)
        try:
            if (cur / ".git").exists():
                return chain
        except OSError:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    return [start] if home is None or start != home else []


def _skill_files(root: Path, how: str, budget: list[int]) -> list[Path]:
    try:
        if not root.is_dir():
            return []
        if how == ONE:
            files = sorted(
                d / SKILL_FILE for d in root.iterdir()
                if d.is_dir() and not d.name.startswith(".") and (d / SKILL_FILE).is_file()
            )
        else:
            files = sorted(f for f in root.rglob(SKILL_FILE) if ".archive" not in f.parts)
    except OSError:
        return []
    take = files[: max(0, budget[0])]
    budget[0] -= len(take)
    return take


def project_skills(folder: Any) -> list[Skill]:
    """The skills a session FOLDER brings with it (Claude Code's
    ``.claude/skills``, the Agent Skills ``.agents/skills``, Grok's, Pi's,
    OpenCode's) from the folder up through its repository root — nearest
    first, one per name (the nearest wins). Tagged :data:`SOURCE_PROJECT`, so
    a model reads them only after the injection scan. Never raises."""
    raw = str(folder or "").strip()
    if not raw:
        return []
    out: list[Skill] = []
    seen: set[str] = set()
    budget = [MAX_PROJECT_FILES]
    try:
        for base in _walk_up(Path(raw)):
            for parts, how in PROJECT_SKILL_DIRS:
                for md in _skill_files(base.joinpath(*parts), how, budget):
                    try:
                        skill = load_skill(md.parent, source=SOURCE_PROJECT)
                    except Exception:  # noqa: BLE001 — a bad SKILL.md is skipped
                        continue
                    key = skill.name.lower()
                    if skill.name and key not in seen:
                        seen.add(key)
                        out.append(skill)
    except Exception:  # noqa: BLE001
        log.warning("reading a folder's skills failed", exc_info=True)
    return out


def session_skills(registry: Any, folder: Any) -> list[Skill]:
    """What a session in ``folder`` can use: the registry's skills, then the
    folder's own whose names the registry does not hold (Claude Code:
    personal before project)."""
    try:
        base = list(registry.list()) if registry is not None else []
    except Exception:  # noqa: BLE001
        base = []
    names = {s.name.lower() for s in base}
    return base + [s for s in project_skills(folder) if s.name.lower() not in names]


def find(registry: Any, name: str, folder: Any = "") -> "Skill | None":
    """The skill called ``name`` for a session in ``folder``: the registry's
    first, then the folder's own. A plugin skill asked for by Claude Code's
    ``<plugin>:<skill>`` name is found by its own name too."""
    wanted = str(name or "").strip()
    if not wanted:
        return None
    candidates = [wanted]
    if ":" in wanted:
        candidates.append(wanted.rsplit(":", 1)[1].strip())
    for cand in candidates:
        try:
            got = registry.get(cand) if registry is not None else None
        except Exception:  # noqa: BLE001
            got = None
        if got is not None:
            return got
    if str(folder or "").strip():
        lowered = {c.lower() for c in candidates}
        for skill in project_skills(folder):
            if skill.name.lower() in lowered:
                return skill
    return None


# --------------------------------------------------------------------------- #
# what the model is told, and what invoking one hands it
# --------------------------------------------------------------------------- #


def base_dir(skill: Skill) -> str:
    """The skill's folder, as the model should use it (absolute; "" when the
    skill has no folder)."""
    raw = getattr(skill, "dir", None)
    if raw is None or not str(raw).strip():
        return ""
    try:
        return str(Path(raw).absolute())
    except (OSError, TypeError, ValueError):
        return str(raw)


def base_dir_line(skill: Skill) -> str:
    """Claude Code's own first line (``Base directory for this skill: …``);
    "" for a skill with no folder."""
    where = base_dir(skill)
    return f"Base directory for this skill: {where}\n\n" if where else ""


def fill(body: str, skill: Skill, args: str = "") -> str:
    """Claude Code's filling of a skill body: ``${CLAUDE_SKILL_DIR}`` (the
    skill's folder, forward slashes, as Claude Code writes it), then
    ``$ARGUMENTS`` / ``$ARGUMENTS[N]`` / ``$N`` (0-based) / named ``$x`` —
    and, when nothing was filled, the words added as ``ARGUMENTS: …``
    (``harness/claude_commands.substitute``, the one filler)."""
    from ..harness.claude_commands import substitute

    where = base_dir(skill)
    text = str(body or "")
    if where:
        text = text.replace("${CLAUDE_SKILL_DIR}", where.replace("\\", "/"))
    return substitute(text, str(args or ""), getattr(skill, "argument_names", ()) or ())


def invocation_text(
    skill: Skill,
    args: str = "",
    *,
    event_bus: Any = None,
    session_id: "str | None" = None,
    cap: "int | None" = INVOKE_CHARS,
) -> str:
    """What invoking ``skill`` hands the model — the base directory line and
    the body with its arguments filled (Claude Code's ``Skill`` tool and its
    ``/name words``; OpenCode's ``skill`` tool names the folder the same
    way). The body is scanned first when the skill did not come from the
    user (``framework.guarded_instructions``); a cut is said."""
    from .framework import guarded_instructions

    body = guarded_instructions(skill, event_bus=event_bus, session_id=session_id, cap=cap)
    cut = bool(cap and len(getattr(skill, "instructions", "") or "") > cap)
    text = base_dir_line(skill) + fill(body, skill, args)
    if cut:
        text += f"\n\n[The rest of SKILL.md is not shown (it is over {cap:,} characters); read it from the base directory.]"
    return text


def _desc(skill: Skill) -> str:
    desc = " ".join(str(skill.description or "").split())
    when = " ".join(str(getattr(skill, "when_to_use", "") or "").split())
    return f"{desc} - {when}" if desc and when else (desc or when)


def listable(skills: Iterable[Skill]) -> list[Skill]:
    """The skills a model may be told about: model-invocable ones."""
    return [s for s in skills if getattr(s, "model_invocable", True)]


def _fit(entries: list[tuple[str, str]], budget: int) -> list[str]:
    """Claude Code's budget: whole entries when they fit; else every
    description cut to the same share; names only when even that is too
    little."""
    lines = [f"- {n}: {d[:LISTING_DESC_CHARS]}" if d else f"- {n}" for n, d in entries]
    if sum(len(x) + 1 for x in lines) <= budget:
        return lines
    names = sum(len(n) + 4 for n, _ in entries)
    share = (budget - names) // max(1, len(entries))
    if share >= 40:
        return [f"- {n}: {d[:share - 1]}…" if len(d) > share else f"- {n}: {d}" for n, d in entries]
    return [f"- {n}" for n, _ in entries]


def listing(skills: Iterable[Skill], *, dialect: str = "", window_tokens: "int | None" = None) -> str:
    """The skills list a harness turn's model reads ("" when none), in the
    words of the harness its dialect speaks: Claude Code's for ``claude``,
    Codex's (with each ``SKILL.md``'s location) for ``gpt``, Claude Code's
    shape naming ``skill_load`` for the rest."""
    shown = listable(skills)
    if not shown:
        return ""
    try:
        window = int(window_tokens or LISTING_DEFAULT_WINDOW)
    except (TypeError, ValueError):
        window = LISTING_DEFAULT_WINDOW
    budget = max(1, int(window * 4 * LISTING_FRACTION))
    if dialect == "gpt":
        entries = [
            (s.name, (_desc(s) + f" (file: {base_dir(s).replace(chr(92), '/')}/SKILL.md)").strip())
            for s in shown
        ]
        return (
            "## Skills\nA skill is a set of local instructions to follow that is stored in a "
            "`SKILL.md` file. Below is the list of skills that can be used. Each entry includes a "
            "name, description, and the location of its `SKILL.md`.\n### Available skills\n"
            + "\n".join(_fit(entries, budget))
            + "\n### How to use skills\n- If the user names a skill or the task clearly matches "
            "one, use it: load it with skill_load (by name) or read its `SKILL.md` completely "
            "before acting. Resolve relative paths against the directory containing that "
            "`SKILL.md` (its scripts/ and references/ live there)."
        )
    tool = "the Skill tool" if dialect == "claude" else "skill_load"
    return (
        f"The following skills are available for use with {tool}:\n\n"
        + "\n".join(_fit([(s.name, _desc(s)) for s in shown], budget))
    )


__all__ = [
    "INVOKE_CHARS",
    "PROJECT_SKILL_DIRS",
    "SOURCE_PROJECT",
    "base_dir",
    "base_dir_line",
    "codex_home",
    "fill",
    "find",
    "invocation_text",
    "listable",
    "listing",
    "project_skills",
    "session_skills",
    "user_roots",
]
