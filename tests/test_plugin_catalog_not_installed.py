"""A cloned plugin MARKETPLACE is a store, not installed skills (v1.98.0).

Adding a marketplace clones its entire catalog into
``~/.claude/plugins/marketplaces/<name>/`` so it can be browsed. Skill discovery
globbed ``~/.claude/plugins/**/SKILL.md``, which cannot tell "available in a
store" from "installed by the user" — so the catalog's sample and authoring
plugins entered the registry, searchable by agents and injected into prompts.

Measured on the machine this was found on: 40 external skills, of which 26 were
catalog content (``plugin-dev``, ``mcp-server-dev``, discord/imessage/telegram,
and one literally named ``example-plugin``) — against 14 real ones.

These tests build a synthetic ``~/.claude`` so they assert the behaviour rather
than one developer's disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.skills.framework import (
    SkillRegistry,
    marketplace_catalog_dirs,
)


def _skill(dir_: Path, name: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} does a thing\n---\n\nBody.\n",
        encoding="utf-8",
    )


@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    """A ~/.claude with one real skill, one directly-installed plugin, and a
    cloned marketplace catalog carrying skills the user never installed."""
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"

    _skill(home / ".claude" / "skills" / "my-real-skill", "my-real-skill")

    # A plugin installed DIRECTLY under plugins/ (not from a catalog clone).
    _skill(plugins / "installed-plugin" / "skills" / "helper", "installed-helper")

    # The cloned catalog: what "add a marketplace" produces.
    mkt = plugins / "marketplaces" / "official"
    (mkt / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (mkt / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "official", "plugins": []}), encoding="utf-8"
    )
    _skill(mkt / "plugins" / "example-plugin" / "skills" / "demo", "catalog-example")
    _skill(mkt / "external_plugins" / "discord" / "skills" / "access", "catalog-discord")
    _skill(mkt / "plugins" / "plugin-dev" / "skills" / "authoring", "catalog-plugin-dev")

    (plugins / "known_marketplaces.json").write_text(
        json.dumps({"official": {"installLocation": str(mkt)}}), encoding="utf-8"
    )

    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def test_catalogs_are_identified_from_the_manifest(fake_home):
    found = marketplace_catalog_dirs()
    mkt = (fake_home / ".claude" / "plugins" / "marketplaces" / "official").resolve()
    assert mkt in found


def test_catalogs_are_still_found_when_the_manifest_is_missing_or_corrupt(fake_home):
    """The conventional layout is the fallback — a catalog missed here would
    silently re-pollute the registry."""
    manifest = fake_home / ".claude" / "plugins" / "known_marketplaces.json"
    manifest.write_text("{not json", encoding="utf-8")
    found = marketplace_catalog_dirs()
    mkt = (fake_home / ".claude" / "plugins" / "marketplaces" / "official").resolve()
    assert mkt in found

    manifest.unlink()
    assert mkt in marketplace_catalog_dirs()


def test_repopulate_keeps_real_skills_and_drops_catalog_ones(fake_home, tmp_path):
    """The whole point: browsing a store must not install its contents."""
    reg = SkillRegistry().repopulate(home=tmp_path / "ijhome")
    names = set(reg._skills)

    assert "my-real-skill" in names
    assert "installed-helper" in names, "a directly-installed plugin must still load"

    assert "catalog-example" not in names
    assert "catalog-discord" not in names
    assert "catalog-plugin-dev" not in names


def test_a_catalog_under_a_nonstandard_path_is_still_excluded(tmp_path, monkeypatch):
    """installLocation can point anywhere; the manifest is authoritative."""
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"
    elsewhere = tmp_path / "somewhere-else" / "cloned-market"
    _skill(elsewhere / "plugins" / "demo" / "skills" / "x", "offsite-catalog-skill")
    _skill(plugins / "real" / "skills" / "y", "offsite-real-skill")
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "known_marketplaces.json").write_text(
        json.dumps({"m": {"installLocation": str(elsewhere)}}), encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    reg = SkillRegistry()
    reg.discover_recursive(plugins, source="claude", exclude=marketplace_catalog_dirs())
    reg.discover_recursive(elsewhere, source="claude", exclude=marketplace_catalog_dirs())
    assert "offsite-real-skill" in reg._skills
    assert "offsite-catalog-skill" not in reg._skills


def test_exclude_defaults_to_off_so_other_callers_are_unaffected(tmp_path):
    """discover_recursive without ``exclude`` behaves exactly as before."""
    root = tmp_path / "r"
    _skill(root / "a" / "skills" / "one", "plain-skill")
    reg = SkillRegistry().discover_recursive(root, source="custom")
    assert "plain-skill" in reg._skills


def test_no_claude_dir_at_all_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "empty"))
    assert marketplace_catalog_dirs() == []
