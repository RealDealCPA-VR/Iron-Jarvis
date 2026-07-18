"""Studio skills must be UNIVERSAL across engines (live-hit 2026-07-18).

Only Claude Code auto-discovers ~/.claude/skills; "Use your 'X' skill" is
meaningless to codex/grok. For every non-Claude engine the first brief now
points the CLI at the skill's SKILL.md ON DISK (every CLI can read a file),
and Auto mode becomes a read-this-file menu of the media skills present."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.creative import (
    _AUTO_SKILL_HINT,
    _portable_skill_line,
)
from iron_jarvis.skills.framework import SkillRegistry


def _registry_with(tmp_path, *names: str) -> SkillRegistry:
    for name in names:
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test skill\n---\ninstructions",
            encoding="utf-8",
        )
    return SkillRegistry().discover(tmp_path / "skills")


def test_named_skill_becomes_a_file_path(tmp_path):
    reg = _registry_with(tmp_path, "pixio-story")
    line = _portable_skill_line(reg, "pixio-story")
    assert "Read the file" in line
    assert str(tmp_path / "skills" / "pixio-story" / "SKILL.md") in line


def test_unknown_skill_degrades_to_the_name_not_a_dead_path(tmp_path):
    reg = _registry_with(tmp_path)  # empty registry
    assert _portable_skill_line(reg, "mystery") == "Use your 'mystery' skill."


def test_auto_menu_lists_only_present_media_skills(tmp_path):
    reg = _registry_with(tmp_path, "pixio-song", "pixio-skill")
    line = _portable_skill_line(reg, "")
    assert "pixio-song" in line and "pixio-skill" in line
    assert "pixio-story" not in line  # not on this machine — no dead paths
    assert str(tmp_path / "skills" / "pixio-song" / "SKILL.md") in line


def test_auto_menu_with_no_media_skills_keeps_the_generic_hint(tmp_path):
    reg = _registry_with(tmp_path)
    assert _portable_skill_line(reg, "") == _AUTO_SKILL_HINT


def test_first_brief_is_per_engine(tmp_path):
    """End to end through /say: a codex-marked session gets the file-path line;
    a claude-marked session keeps the native skill reference."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    skill_dir = tmp_path / "skills" / "pixio-story"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pixio-story\ndescription: video\n---\ngo", encoding="utf-8"
    )
    platform.skills.discover(tmp_path / "skills")

    for cli, expect in (
        ("codex", str(skill_dir / "SKILL.md")),
        ("claude", "Use your 'pixio-story' skill."),
    ):
        tid = client.post("/terminals", json={"cwd": str(tmp_path)}).json()["id"]
        try:
            session = platform.terminals.get(tid)
            setattr(session, "_studio_cli", cli)
            typed: list[str] = []
            orig_write = session.write
            session.write = lambda data, _t=typed: _t.append(
                data if isinstance(data, str) else data.decode("utf-8", "replace")
            )
            r = client.post(
                f"/creative/studio/{tid}/say",
                json={"text": "make a video", "first": True, "skill": "pixio-story"},
            )
            assert r.status_code == 200, r.text
            combined = "".join(typed)
            assert expect in combined, f"{cli}: {expect!r} not in brief"
            session.write = orig_write
        finally:
            client.delete(f"/terminals/{tid}")
