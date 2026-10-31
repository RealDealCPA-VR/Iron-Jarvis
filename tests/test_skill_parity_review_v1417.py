# ruff: noqa: F811 — the imported fixtures are parameters below.
"""v1.417.0 review — the defects an independent read of the skill-parity
change found, each pinned red-first.

1. THE LIST IS SCANNED. ``native.listing`` put every listed skill's NAME and
   DESCRIPTION into a coding session's system prompt verbatim — including a
   cloned repository's own ``.agents/skills`` / ``.claude/skills`` and the
   skills of other programs' folders (Codex, Grok, Pi, OpenCode, plugins),
   which ``framework.scans_skill`` says must be scanned. The harness layer's
   own folder skills always went through promptguard
   (``harness/components``); a planted description now does too, and
   ``skill_search``'s answer reads the same scanned words.
2. A FOLDER'S SKILLS STAY IN THE FOLDER. ``project_skills`` followed a
   junction / symlink anywhere (``.claude/skills`` -> another folder) and
   read files the user's "Folders Jarvis may read" setting refuses (the walk
   goes UP to the repository root, above the session's folder). A link is
   not followed (it is never opened, so a link to a network share is never
   touched) and every file passes ``fs_read_ok``.
3. A "/skill" FITS THE MODEL. The playbook went from a fixed 8,000 characters
   to 100,000 whatever the model — a 37 KB skill on an 8K-token local model
   is a system prompt the history planner cannot shrink, so the turn could
   only fail. The cut is now a quarter of the answering model's window (never
   below the old 8,000, never above 100,000), said when it cuts.
4. (the coordinator's check) synced claude.ai skills follow the signed-in
   account read from ``.claude.json`` — ``CLAUDE_CODE_*_UUID`` (a parent
   session's) are ignored; no readable account offers every bucket.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from iron_jarvis.skills import native
from iron_jarvis.skills.framework import SkillRegistry
from iron_jarvis.skills.loader import load_skill
# The ``home`` (a scratch user home) and ``plat`` fixtures are the parity
# file's own, imported so pytest finds them here (only for this module).
from tests.test_skill_parity_v1417 import (  # noqa: F401
    ACCT, INJ, ORG, OTHER, _ctx, home, mkskill, plat, write,
)


def _junction(link: Path, target: Path) -> bool:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return r.returncode == 0
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 1. the list is scanned
# --------------------------------------------------------------------------- #


def test_a_planted_description_never_reaches_the_listing(tmp_path):
    ws = tmp_path / "ws"
    mkskill(ws / ".agents" / "skills" / "helper", "helper", description=f"Formats code. {INJ}")
    skills = native.project_skills(ws)
    assert [s.name for s in skills] == ["helper"]
    for dialect in ("claude", "gpt", "generic"):
        text = native.listing(skills, dialect=dialect)
        assert "- helper" in text
        assert INJ not in text and "[BLOCKED" in text, dialect


def test_the_users_own_skill_is_listed_verbatim(tmp_path):
    sk = load_skill(mkskill(tmp_path / "mine", "mine", description=f"Mine. {INJ}"), source="user")
    assert INJ in native.listing([sk], dialect="claude")


def test_a_flagged_name_drops_the_entry(tmp_path):
    sk = load_skill(mkskill(tmp_path / "x", "x", description="ok"), source="project")
    sk.name = INJ
    assert native.listing([sk], dialect="claude") == ""


async def test_skill_search_answers_with_the_scanned_description(plat):
    platform, ws, _ = plat
    mkskill(ws / ".agents" / "skills" / "lintfix", "lintfix", description=f"lintfix tidies code. {INJ}")
    found = await platform.registry.invoke("skill_search", {"query": "lintfix tidies"}, _ctx(platform, ws),
                                           platform.permissions)
    assert "lintfix" in found.output
    assert INJ not in found.output and "[BLOCKED" in found.output


# --------------------------------------------------------------------------- #
# 2. a folder's skills stay in the folder
# --------------------------------------------------------------------------- #


def test_a_linked_skills_folder_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    mkskill(outside / "evil", "evil", body="OUTSIDE BODY")
    ws = tmp_path / "ws"
    ws.mkdir()
    if not _junction(ws / ".claude" / "skills", outside):
        pytest.skip("no junction / symlink here")
    if not _junction(ws / ".agents" / "skills" / "via", outside):
        pytest.skip("no junction / symlink here")
    mkskill(ws / ".agents" / "skills" / "real" / "inside", "inside")
    names = [s.name for s in native.project_skills(ws)]
    assert names == ["inside"], names


def test_a_skill_outside_the_readable_folders_is_not_read(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sub = repo / "sub"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()
    mkskill(repo / ".claude" / "skills" / "above", "above")
    mkskill(sub / ".claude" / "skills" / "here", "here")
    monkeypatch.setenv("IRONJARVIS_FS_ALLOWLIST", str(sub))
    assert [s.name for s in native.project_skills(sub)] == ["here"]


# --------------------------------------------------------------------------- #
# 3. a "/skill" fits the model
# --------------------------------------------------------------------------- #


def test_the_invoke_cap_follows_the_window():
    assert native.invoke_cap(None) == 32_000
    assert native.invoke_cap(8_000) == 8_000          # never below the old 8,000
    assert native.invoke_cap(64_000) == 64_000
    assert native.invoke_cap(200_000) == native.INVOKE_CHARS
    assert native.invoke_cap(1_000_000) == native.INVOKE_CHARS


async def test_a_long_slash_skill_is_cut_to_a_small_models_window_in_both_lanes(plat, monkeypatch):
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from tests._fakes.harness_lane import wire

    platform, ws, sk = plat
    mkskill(Path(sk).parent / "huge", "huge", body="HEAD " + ("filler " * 8000) + "TAIL-MARK-HUGE")
    platform.skills.repopulate(platform.config.home)
    platform.config.model_context_windows = {platform.config.default_provider: 8_000}
    for lane in ("post", "stream"):
        model = wire(platform, monkeypatch, ["ok"])
        b = ChatBody(messages=[ChatMessageBody(role="user", content="go")], skill="huge", skill_args="")
        if lane == "post":
            await run_chat_turn(platform, BUILTIN_PERSONAS, b)
        else:
            async for _ in await stream_chat_turn(platform, BUILTIN_PERSONAS, b):
                pass
        system = model.calls[0]["system"]
        part = system.split("# Skill invoked by the user: huge", 1)[1]
        assert "HEAD " in part and "TAIL-MARK-HUGE" not in part, lane
        assert len(part) < 9_000, (lane, len(part))
        assert "is not shown" in part, lane


# --------------------------------------------------------------------------- #
# 4. synced skills: the signed-in account, as Claude Code reads it
# --------------------------------------------------------------------------- #


def test_a_parent_sessions_account_variables_are_ignored(home, tmp_path, monkeypatch):
    write(home / ".claude.json", json.dumps({"oauthAccount": {"organizationUuid": ORG, "accountUuid": ACCT}}))
    monkeypatch.setenv("CLAUDE_CODE_ORGANIZATION_UUID", ORG)
    monkeypatch.setenv("CLAUDE_CODE_ACCOUNT_UUID", OTHER)
    synced = home / ".claude" / "skills" / "synced"
    mkskill(synced / f"{ORG}_{ACCT}" / "mine", "mine")
    mkskill(synced / f"{ORG}_{OTHER}" / "theirs", "theirs")
    reg = SkillRegistry().repopulate(tmp_path / "ij-home")
    assert reg.get("mine") is not None and reg.get("theirs") is None


def test_with_no_readable_account_every_bucket_loads(home, tmp_path):
    synced = home / ".claude" / "skills" / "synced"
    mkskill(synced / f"{ORG}_{ACCT}" / "mine", "mine")
    mkskill(synced / f"{ORG}_{OTHER}" / "theirs", "theirs")
    reg = SkillRegistry().repopulate(tmp_path / "ij-home")
    assert reg.get("mine") is not None and reg.get("theirs") is not None


async def test_a_long_message_is_not_copied_whole_into_the_system_prompt(plat, monkeypatch):
    """The chat page sends the words after "/name" as the message; with no
    ``$ARGUMENTS`` in the body they ride the playbook as ``ARGUMENTS: …``
    too. The message is ALSO the conversation's last turn (which the planner
    budgets) — the copy in the system prompt (which it cannot) is bounded by
    the same cap as the body."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from tests._fakes.harness_lane import wire

    platform, ws, sk = plat
    platform.config.model_context_windows = {platform.config.default_provider: 8_000}
    pasted = "START-OF-PASTE " + ("row of a pasted table " * 3000) + "END-OF-PASTE"
    for lane in ("post", "stream"):
        model = wire(platform, monkeypatch, ["ok"])
        b = ChatBody(messages=[ChatMessageBody(role="user", content=pasted)], skill="plain")
        if lane == "post":
            await run_chat_turn(platform, BUILTIN_PERSONAS, b)
        else:
            async for _ in await stream_chat_turn(platform, BUILTIN_PERSONAS, b):
                pass
        part = model.calls[0]["system"].split("# Skill invoked by the user: plain", 1)[1]
        assert "No placeholders here." in part and "ARGUMENTS: START-OF-PASTE" in part, lane
        assert "END-OF-PASTE" not in part and len(part) < 9_000, (lane, len(part))
