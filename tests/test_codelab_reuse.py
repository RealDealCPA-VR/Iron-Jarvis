"""Agents reuse what they already built (v1.97.0).

The Code Lab was write-only from the agent's side: every run_code run was saved,
but nothing could read it back, so the same blocker produced the same script
again next week. ``code_search`` / ``code_load`` / ``code_run`` close that loop.

The trust split is the part worth guarding hardest: reading is cheap and
allowed; RUNNING saved code is exactly run_code's power and must stay gated.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.codelab.store import CodeArtifactStore
from iron_jarvis.codelab.tools import code_tools
from iron_jarvis.core.db import open_db
from iron_jarvis.tools.base import Reversibility, ToolContext


@pytest.fixture
def store(tmp_path) -> CodeArtifactStore:
    s = CodeArtifactStore(open_db(tmp_path / "t.db"))
    s.save("merge_csvs", "python", "import csv  # openpyxl not needed\nprint('merged')",
           description="Merge the quarterly CSV exports into one workbook",
           exit_code=0, output="merged")
    s.save("broken_resize", "python", "raise SystemExit(2)",
           description="Resize the marketing images to 1080p",
           exit_code=2, output="boom")
    s.save("rename_invoices", "powershell", "Get-ChildItem",
           description="Rename scanned invoices to INV-<date>.pdf",
           exit_code=0, output="")
    return s


@pytest.fixture
def tools(store, tmp_path):
    return {t.name: t for t in code_tools(store, tmp_path / "codelab")}


def _ctx(tmp_path) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r",
                       config=None, event_bus=None, engine=None)


# --- ranking ---------------------------------------------------------------


def test_search_finds_prior_art_by_purpose(store):
    hits = store.search("merge the csv exports")
    assert hits and hits[0].name == "merge_csvs"


def test_search_also_matches_the_source_not_just_the_purpose(store):
    """"openpyxl" appears only in the code — an agent recalling a library
    should still find the script that used it."""
    hits = store.search("openpyxl")
    assert [h.name for h in hits] == ["merge_csvs"]


def test_working_scripts_outrank_broken_ones(store):
    """A script that last failed is still findable — it may be the closest prior
    art — but something proven must come first."""
    store.save("resize_ok", "python", "print('ok')",
               description="Resize the marketing images to 1080p", exit_code=0)
    hits = store.search("resize the marketing images")
    assert hits[0].name == "resize_ok"
    assert "broken_resize" in [h.name for h in hits]  # not hidden


def test_empty_query_returns_nothing_rather_than_everything(store):
    assert store.search("") == []
    assert store.search("   ") == []


# --- the tools -------------------------------------------------------------


def test_code_search_reports_whether_each_hit_ACTUALLY_worked(tools, tmp_path):
    """A hit is prior art, not a promise. The failure must be visible or an
    agent will reuse broken code believing it is proven."""
    res = asyncio.run(tools["code_search"].execute(
        {"query": "resize the marketing images"}, _ctx(tmp_path)))
    assert res.ok
    assert "FAILED (exit 2)" in res.output
    assert res.data["artifacts"][0]["last_exit_code"] == 2


def test_code_search_miss_tells_the_agent_what_to_do_next(tools, tmp_path):
    res = asyncio.run(tools["code_search"].execute(
        {"query": "quantum teleportation"}, _ctx(tmp_path)))
    assert res.ok  # a miss is not an error
    assert res.data["artifacts"] == []
    assert "run_code" in res.output and "purpose" in res.output


def test_code_load_returns_full_source_with_its_status(tools, store, tmp_path):
    rec = store.search("merge the csv exports")[0]
    res = asyncio.run(tools["code_load"].execute({"id": rec.id}, _ctx(tmp_path)))
    assert res.ok
    assert "print('merged')" in res.data["source"]
    assert "worked (exit 0)" in res.output


def test_code_run_reruns_the_saved_script_and_records_it(tools, store, tmp_path):
    rec = store.search("merge the csv exports")[0]
    before = rec.run_count
    res = asyncio.run(tools["code_run"].execute({"id": rec.id}, _ctx(tmp_path)))
    assert res.ok, res.error
    assert "merged" in res.output
    assert store.get(rec.id).run_count == before + 1


def test_code_run_surfaces_a_failing_script_honestly(tools, store, tmp_path):
    rec = [r for r in store.list() if r.name == "broken_resize"][0]
    res = asyncio.run(tools["code_run"].execute({"id": rec.id}, _ctx(tmp_path)))
    assert res.ok is False
    assert "exited 2" in (res.error or "")


def test_unknown_ids_are_errors_not_crashes(tools, tmp_path):
    ctx = _ctx(tmp_path)
    assert asyncio.run(tools["code_load"].execute({"id": "nope"}, ctx)).ok is False
    assert asyncio.run(tools["code_run"].execute({"id": "nope"}, ctx)).ok is False


# --- the trust split (guard this hardest) -----------------------------------


def test_reading_is_allowed_but_RUNNING_saved_code_asks_first():
    """code_run executes arbitrary saved code — the same power as run_code and
    shell. If this ever silently becomes "allow", an agent could execute any
    stored script with no consent."""
    from iron_jarvis.core.config import Config

    perms = Config.model_fields["permissions"].default_factory()
    assert perms["code_search"] == "allow"
    assert perms["code_load"] == "allow"
    assert perms["code_run"] == "ask" == perms["run_code"]


def test_code_run_is_never_auto_armed_in_chat():
    """chat's auto-arming curates to fs-confined/read-only tools; executing
    saved code belongs behind the explicit "+" like shell."""
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

    assert "code_search" in AUTO_SAFE_TOOLS
    assert "code_load" in AUTO_SAFE_TOOLS
    assert "code_run" not in AUTO_SAFE_TOOLS
    assert "shell" not in AUTO_SAFE_TOOLS  # the rule this mirrors


def test_reversibility_is_declared_honestly():
    from iron_jarvis.codelab.tools import CodeLoadTool, CodeRunTool, CodeSearchTool

    assert CodeSearchTool(None).reversibility is Reversibility.READONLY
    assert CodeLoadTool(None).reversibility is Reversibility.READONLY
    # Fail-safe: a re-run leaves real effects and must never claim to be undoable.
    assert CodeRunTool(None, None).reversibility is Reversibility.IRREVERSIBLE


# --- wiring ----------------------------------------------------------------


def test_the_tools_are_actually_registered_and_offered_to_agents(tmp_path):
    """Registration + agent tool-list membership, so the loop is reachable in
    the real product rather than only in this test file."""
    from iron_jarvis.agents.types import get_agent_definition
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    for name in ("code_search", "code_load", "code_run"):
        assert platform.registry.get(name) is not None, f"{name} not registered"

    builder = get_agent_definition(AgentType.BUILDER)
    assert "code_search" in builder.tools


def test_run_code_steers_the_agent_to_check_for_prior_art_first(tmp_path):
    """The behavior the user asked for lives in the tool description the model
    actually reads — assert it, so a future edit can't quietly drop it."""
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    desc = platform.registry.get("run_code").description
    assert "code_search" in desc
