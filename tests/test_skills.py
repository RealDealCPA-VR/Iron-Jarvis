"""Tests for the Skills Framework (§23)."""

from __future__ import annotations

import pytest

from iron_jarvis.core.config import load_config
from iron_jarvis.skills import framework
from iron_jarvis.skills.framework import SkillRegistry
from iron_jarvis.skills.loader import load_skill
from iron_jarvis.skills.tools import SkillLoadTool, SkillSearchTool, skill_tools
from iron_jarvis.tools.base import ToolContext


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry().discover(SkillRegistry.builtin_dir() or framework.builtin_dir())


@pytest.fixture
def ctx(tmp_path) -> ToolContext:
    # Skill tools don't touch event_bus/engine; build a real-ish context anyway.
    config = load_config(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=config,
        event_bus=None,
        engine=None,
    )


def test_discover_finds_builtin_skills(registry):
    names = {s.name for s in registry.list()}
    assert "research" in names
    assert "financial-analysis" in names


def test_load_skill_parses_frontmatter_and_body():
    research_dir = framework.builtin_dir() / "research"
    skill = load_skill(research_dir)
    assert skill.name == "research"
    assert skill.description
    assert skill.instructions
    assert "Deep Research" in skill.instructions


def test_load_skill_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path)


def test_search_ranks_research_first(registry):
    hits = registry.search("research investigation sources")
    assert hits
    assert hits[0].name == "research"


def test_inject_appends_skill_instructions(registry):
    skill = registry.get("research")
    out = registry.inject("BASE PROMPT", ["research"])
    assert "BASE PROMPT" in out
    assert "# Skills" in out
    assert skill.instructions in out


def test_inject_ignores_unknown_skill(registry):
    assert registry.inject("BASE", ["does-not-exist"]) == "BASE"


async def test_skill_search_tool(registry, ctx):
    tool = SkillSearchTool(registry)
    result = await tool.execute({"query": "research investigation sources"}, ctx)
    assert result.ok
    assert "research" in result.output
    assert result.data["skills"][0]["name"] == "research"


async def test_skill_load_tool(registry, ctx):
    tool = SkillLoadTool(registry)
    result = await tool.execute({"name": "financial-analysis"}, ctx)
    assert result.ok
    assert "Financial Statement Analysis" in result.output
    assert result.data["name"] == "financial-analysis"


async def test_skill_load_tool_unknown(registry, ctx):
    tool = SkillLoadTool(registry)
    result = await tool.execute({"name": "nope"}, ctx)
    assert not result.ok
    assert "unknown skill" in (result.error or "")


def test_skill_tools_factory(registry):
    tools = skill_tools(registry)
    assert {t.name for t in tools} == {"skill_search", "skill_load"}
