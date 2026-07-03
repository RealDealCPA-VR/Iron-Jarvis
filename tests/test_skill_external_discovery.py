"""Recursive skill discovery (Claude/Codex/nested SKILL.md) + source tagging."""

from __future__ import annotations

from pathlib import Path

from iron_jarvis.skills import SkillRegistry
from iron_jarvis.skills import framework as fw


def _write_skill(dir: Path, name: str, desc: str = "d") -> None:
    dir.mkdir(parents=True, exist_ok=True)
    (dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\nInstructions for {name}.",
        encoding="utf-8",
    )


def test_discover_recursive_finds_deeply_nested(tmp_path):
    _write_skill(tmp_path / "a" / "b" / "c", "deep")
    reg = SkillRegistry().discover_recursive(tmp_path, source="claude")
    sk = reg.get("deep")
    assert sk is not None
    assert sk.source == "claude"


def test_recursive_is_first_wins_over_existing(tmp_path):
    # A builtin-style skill registered first must NOT be clobbered by an external.
    _write_skill(tmp_path / "user" / "shared", "shared", "the user one")
    _write_skill(tmp_path / "ext" / "nested" / "shared", "shared", "the external one")
    reg = SkillRegistry()
    reg.discover(tmp_path / "user", source="user")
    reg.discover_recursive(tmp_path / "ext", source="claude")
    sk = reg.get("shared")
    assert sk.source == "user"
    assert sk.description == "the user one"


def test_repopulate_pulls_external_roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_skill(home / "skills" / "mine", "mine")
    fake_claude = tmp_path / "claude"
    _write_skill(fake_claude / "web-research", "web-research")
    fake_codex = tmp_path / "codex"
    _write_skill(fake_codex / ".system" / "imagegen", "imagegen")
    monkeypatch.setattr(
        fw,
        "external_skill_roots",
        lambda: [(fake_claude, "claude"), (fake_codex, "codex")],
    )
    reg = SkillRegistry().repopulate(home)
    names = {s.name: s.source for s in reg.list()}
    assert names.get("mine") == "user"
    assert names.get("web-research") == "claude"
    assert names.get("imagegen") == "codex"


def test_repopulate_is_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "external_skill_roots", lambda: [])
    home = tmp_path / "home"
    _write_skill(home / "skills" / "one", "one")
    reg = SkillRegistry().repopulate(home)
    ident = id(reg._skills)
    _write_skill(home / "skills" / "two", "two")
    reg.repopulate(home)
    assert id(reg._skills) == ident  # same dict object — bound tools keep working
    assert reg.get("two") is not None


def test_extra_paths_scanned_as_custom(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "external_skill_roots", lambda: [])
    home = tmp_path / "home"
    extra = tmp_path / "somewhere" / "deep"
    _write_skill(extra / "handy", "handy")
    reg = SkillRegistry().repopulate(home, [str(tmp_path / "somewhere")])
    sk = reg.get("handy")
    assert sk is not None and sk.source == "custom"
