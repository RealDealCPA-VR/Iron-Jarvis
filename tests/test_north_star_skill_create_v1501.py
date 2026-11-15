"""North-star fix 1.10 (v1.501.0): ``skill_create`` never replaces a skill
silently.

A second ``skill_create`` with the name of a skill the user already has used
to overwrite its SKILL.md in place, with no earlier version kept and nothing
said. Now it refuses in plain words, names ``skill_update`` (which keeps the
version before the change in ``.history``), and leaves the first file
byte-for-byte as it was. Driven through the real tool registry.
"""

from __future__ import annotations

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.skills.loader import SKILL_FILE, slugify
from iron_jarvis.tools.base import ToolContext


@pytest.fixture()
def platform(tmp_path):
    return build_platform(str(tmp_path / "home"))


def _ctx(p, tmp_path, session_id="sess-agent-1") -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(workspace=ws, session_id=session_id, agent_run_id="run-1",
                       config=p.config, event_bus=p.event_bus, engine=p.engine)


async def _invoke(p, ctx, tool, **args):
    return await p.registry.invoke(tool, args, ctx, p.permissions,
                                   allowed_names={tool}, session_allow={tool})


async def test_a_second_create_with_the_same_name_is_refused_and_leaves_the_first(platform, tmp_path):
    ctx = _ctx(platform, tmp_path)
    first = await _invoke(platform, ctx, "skill_create", name="Month End Close",
                          description="Close the month.", instructions="1. Reconcile the bank.")
    assert first.ok, first.error
    path = platform.config.home / "skills" / slugify("Month End Close") / SKILL_FILE
    before = path.read_bytes()

    again = await _invoke(platform, ctx, "skill_create", name="Month End Close",
                          description="Something else.", instructions="Replaced text.")
    assert again.ok is False
    assert "skill_update" in (again.error or ""), again.error
    assert "already" in (again.error or "").lower(), again.error
    assert path.read_bytes() == before
    # Another spelling of the same folder name is the same skill.
    other = await _invoke(platform, ctx, "skill_create", name="month end close",
                          description="x", instructions="Replaced again.")
    assert other.ok is False and path.read_bytes() == before


async def test_a_refused_create_saves_no_script_either(platform, tmp_path):
    ctx = _ctx(platform, tmp_path)
    assert (await _invoke(platform, ctx, "skill_create", name="Totals", description="d",
                          instructions="Add up.")).ok
    script_dir = platform.config.home / "skills" / slugify("Totals")
    before = sorted(p.name for p in script_dir.rglob("*"))
    again = await _invoke(platform, ctx, "skill_create", name="Totals", description="d",
                          instructions="Add up.", script="print('hi')\n")
    assert again.ok is False
    assert sorted(p.name for p in script_dir.rglob("*")) == before


async def test_skill_update_still_changes_it_and_keeps_the_version_before(platform, tmp_path):
    ctx = _ctx(platform, tmp_path)
    assert (await _invoke(platform, ctx, "skill_create", name="Payroll", description="d",
                          instructions="Old steps.")).ok
    skill_dir = platform.config.home / "skills" / slugify("Payroll")
    before = (skill_dir / SKILL_FILE).read_bytes()
    r = await _invoke(platform, ctx, "skill_update", name="Payroll", instructions="New steps.")
    assert r.ok, r.error
    assert "New steps." in (skill_dir / SKILL_FILE).read_text(encoding="utf-8")
    kept = [p.read_bytes() for p in (skill_dir / ".history").iterdir()]
    assert before in kept


async def test_a_new_name_is_still_created(platform, tmp_path):
    ctx = _ctx(platform, tmp_path)
    r = await _invoke(platform, ctx, "skill_create", name="Brand New", description="d",
                      instructions="Steps.")
    assert r.ok, r.error
    assert platform.skills.get("Brand New") is not None
