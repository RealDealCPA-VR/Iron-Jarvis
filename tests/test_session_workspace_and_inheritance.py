"""Three orchestrator defects from the 2026-08-20 deep review (findings 01/06/14).

1. ``delete_session`` rmtree'd whatever ``workspace_path`` held once no other
   Session row referenced it. For a session created with ``workspace_root=``
   (every Projects in-folder task, every chat escalation carrying its folder)
   that path is the USER'S REAL FOLDER — and since the session's own row is
   deleted in the preceding transaction, the "is it shared?" guard is empty by
   construction for the last session pointing at it. The Kanban bulk
   "clear completed" walks every finished session through this one by one.

2. ``continue_session`` copied task/agent/provider/model/project/max_steps but
   NOT ``allow_tools_json`` and NOT ``origin`` — so turn 2+ of an escalated
   chat (the dashboard posts /continue for every follow-up) lost the user's
   conversation grant AND, being unattributed, could no longer even ASK:
   ``runtime._pause_for_approval`` pauses only for an origin asserting a
   watching human, so the ask-tier call was denied headlessly.

3. ``rerun_session`` had the mirror half-bug: it carried the grant but passed
   no origin, so a rerun of a chat/job/project session became unattributed.
"""

from __future__ import annotations

import json
from pathlib import Path

from iron_jarvis.agents.orchestrator import Orchestrator, is_managed_workspace
from iron_jarvis.core.models import AgentType

#: The origins ``runtime._pause_for_approval`` accepts as "a human is watching".
#: Kept here so these tests fail if inheritance ever yields a value that cannot
#: restore the pause (e.g. the literal "continuation").
_WATCHED_ORIGIN_PREFIXES = ("chat", "job", "project", "user")


# --- finding 01: a direct workspace is the user's data, not our scratch ------


async def test_delete_session_never_deletes_a_direct_workspace(platform, tmp_path):
    folder = tmp_path / "ClientTaxes"
    folder.mkdir()
    doc = folder / "K-1.pdf"
    doc.write_text("real client data")

    orch = Orchestrator(platform)
    sess = await orch.create_session(
        "summarize these returns", AgentType.BUILDER, workspace_root=str(folder)
    )
    assert Path(sess.workspace_path).resolve() == folder.resolve()

    orch.delete_session(sess.id)

    assert orch.get_session(sess.id) is None  # the session row still goes away
    assert folder.is_dir()  # …the user's folder does NOT
    assert doc.read_text() == "real client data"


async def test_delete_session_still_removes_a_managed_workspace(platform):
    """The cleanup this method exists for must keep working."""
    orch = Orchestrator(platform)
    sess = await orch.run("scratch task", AgentType.BUILDER)
    ws = Path(sess.workspace_path)
    assert ws.is_dir()

    orch.delete_session(sess.id)

    assert not ws.exists()


async def test_delete_of_a_continued_direct_session_spares_the_folder(platform, tmp_path):
    """The shared-path guard cannot save a direct folder: deleting the LAST
    referencing session is exactly what bulk 'clear completed' guarantees."""
    folder = tmp_path / "project-root"
    folder.mkdir()
    (folder / "notes.md").write_text("keep me")

    orch = Orchestrator(platform)
    s1 = await orch.create_session("task", AgentType.BUILDER, workspace_root=str(folder))
    await orch.run_session(s1.id)
    s2 = await orch.continue_session(s1.id, "and one more thing")
    assert Path(s2.workspace_path).resolve() == folder.resolve()

    orch.delete_session(s1.id)
    orch.delete_session(s2.id)  # now nothing references the folder

    assert (folder / "notes.md").read_text() == "keep me"


def test_is_managed_workspace_refuses_to_guess(platform, tmp_path):
    managed = Path(platform.config.workspaces_dir)
    assert is_managed_workspace(platform.config, str(managed / "sess_abc"))
    # The managed ROOT itself is not a session workspace — deleting it would
    # take every other session's workspace with it.
    assert not is_managed_workspace(platform.config, str(managed))
    assert not is_managed_workspace(platform.config, str(tmp_path / "user-folder"))
    assert not is_managed_workspace(platform.config, "")
    assert not is_managed_workspace(platform.config, None)


# --- findings 06 + 14: the follow-up inherits the grant AND the origin -------


async def test_continue_session_inherits_origin_and_grants(platform):
    orch = Orchestrator(platform)
    s1 = await orch.create_session(
        "rename the files",
        AgentType.BUILDER,
        allow_tools=["shell", "write_document"],
        origin="chat",
    )
    await orch.run_session(s1.id)

    s2 = await orch.continue_session(s1.id, "now also do X")

    assert json.loads(s2.allow_tools_json) == ["shell", "write_document"]
    assert s2.origin == "chat"
    # NOT the literal "continuation": that value is not in the runtime's
    # pause allowlist, so it would convert an honest instant denial into a
    # silent 300s pause ending in timeout-deny.
    assert s2.origin != "continuation"
    assert s2.origin.startswith(_WATCHED_ORIGIN_PREFIXES)


async def test_continue_chain_keeps_carrying_origin_and_grants(platform):
    """The dashboard chains to the RETURNED session id, so turn 3 continues
    turn 2 — inheritance has to survive the whole chain, not just one hop."""
    orch = Orchestrator(platform)
    s1 = await orch.create_session(
        "step one", AgentType.BUILDER, allow_tools=["shell"], origin="job:abc123"
    )
    await orch.run_session(s1.id)
    s2 = await orch.continue_session(s1.id, "step two")
    await orch.run_session(s2.id)
    s3 = await orch.continue_session(s2.id, "step three")

    assert s3.origin == "job:abc123"
    assert json.loads(s3.allow_tools_json) == ["shell"]


async def test_continue_session_invents_nothing_for_an_unattributed_run(platform):
    """Presence is asserted, never assumed — a headless parent stays headless."""
    orch = Orchestrator(platform)
    s1 = await orch.run("plain task", AgentType.BUILDER)
    s2 = await orch.continue_session(s1.id, "follow up")

    assert s2.origin is None
    assert json.loads(s2.allow_tools_json) == []


async def test_rerun_session_inherits_origin(platform):
    orch = Orchestrator(platform)
    s1 = await orch.create_session(
        "weekly report", AgentType.BUILDER, allow_tools=["shell"], origin="job:xyz"
    )
    await orch.run_session(s1.id)

    s2 = await orch.rerun_session(s1.id)

    assert s2.origin == "job:xyz"  # a rerun is watched by the same human
    assert s2.origin.startswith(_WATCHED_ORIGIN_PREFIXES)
    assert json.loads(s2.allow_tools_json) == ["shell"]


async def test_rerun_of_an_unattributed_session_stays_unattributed(platform):
    orch = Orchestrator(platform)
    s1 = await orch.run("plain task", AgentType.BUILDER)
    s2 = await orch.rerun_session(s1.id)

    assert s2.origin is None
