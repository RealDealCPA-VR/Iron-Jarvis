"""Wave C (readiness plan 0h.5, item 28): a memory base added on the Memory
page is READ BACK — "added and answering", or why not, in words — exactly
as chat's ``memory_base_add`` says it, and the two boot-time settings rows
(``obsidian_vault``, ``notion_database_id``) say they need a restart and
name the Memory page's "Add a memory base" as the door that works at once.

ONE reading: ``routes.knowledge._base_health`` is what the ``GET
/ltm/sources`` listing asks per base AND what ``read_back`` asks right after
an add; ``add_ltm_source_record`` (the one add both doors call) answers
``answering`` + ``words``. Nothing here is a copy of the listing's rules.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import knowledge as K
from iron_jarvis.settings import records_connections as RC
from iron_jarvis.settings import schema as S
from iron_jarvis.tools.base import ToolContext


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "ij")))


def _ctx(platform, tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine,
    )


# --------------------------------------------------------------------------- #
# The Memory page's door answers whether the new base answers
# --------------------------------------------------------------------------- #
def test_a_folder_that_is_there_is_added_and_answering(client, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# Notes\n", encoding="utf-8")
    res = client.post("/ltm/sources", json={"name": "vault", "kind": "markdown", "path": str(vault)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "vault" and body["kind"] == "markdown"
    assert body["answering"] is True
    assert body["words"] == K.ADDED_ANSWERING == "Added and answering."


def test_a_folder_that_is_not_there_is_added_but_says_it_does_not_answer(client, tmp_path):
    gone = tmp_path / "moved-away"
    res = client.post("/ltm/sources", json={"name": "gone", "kind": "markdown", "path": str(gone)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answering"] is False
    assert body["words"].startswith("Added, but it is not answering: ")
    assert "folder not found" in body["words"] and str(gone) in body["words"]
    # The listing's own reading agrees (one function, two callers).
    listing = client.get("/ltm/sources").json()
    row = next(b for b in listing["bases"] if b["name"] == "gone")
    assert row["available"] is False and row["detail"] in body["words"]


def test_read_back_reads_through_the_listings_own_health_function(client, tmp_path, monkeypatch):
    """The read-back is the listing's ``_base_health``: stub it and both say the stub."""
    vault = tmp_path / "vault"
    vault.mkdir()
    seen: list[str] = []

    def fake_health(conn, kind, *, probe, refresh, deadline):
        seen.append(f"{kind}:{probe}:{refresh}")
        return {"available": False, "detail": "the stub said no"}

    monkeypatch.setattr(K, "_base_health", fake_health)
    res = client.post("/ltm/sources", json={"name": "stubbed", "kind": "markdown", "path": str(vault)})
    assert res.json()["answering"] is False and "the stub said no" in res.json()["words"]
    assert seen and seen[0] == "markdown:True:True", "the add PROBES (fresh), never a cached verdict"
    listing = client.get("/ltm/sources").json()
    row = next(b for b in listing["bases"] if b["name"] == "stubbed")
    assert row["detail"] == "the stub said no", "the listing reads through the same function"


def test_a_base_no_probe_can_reach_is_said_as_not_checked(client, tmp_path):
    res = client.post(
        "/ltm/sources",
        json={"name": "rag", "kind": "http_rag", "endpoint_url": "https://rag.example.com/q", "token": "t-wave-c"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["answering"] is None
    assert res.json()["words"] == K.ADDED_UNCHECKED


def test_the_words_table():
    assert K.added_words(True) == "Added and answering."
    assert K.added_words(False, "folder not found: D:\\x") == "Added, but it is not answering: folder not found: D:\\x."
    assert K.added_words(False, "") == "Added, but it is not answering: it did not answer."
    assert K.added_words(None) == "Added. It is not checked until you search it."
    assert K.added_words(None, "not checked yet — budget") == (
        "Added. It is not checked until you search it. (not checked yet — budget.)"
    )


def test_a_base_the_live_set_does_not_hold_is_not_live(client):
    d = SimpleNamespace(platform=client.app.state.platform)
    back = K.read_back(d, "no-such-base")
    assert back["answering"] is False and back["words"].startswith("Added, but it is not live yet: ")


# --------------------------------------------------------------------------- #
# Chat's memory_base_add says the SAME words
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_chats_memory_base_add_carries_the_pages_words(client, tmp_path):
    platform = client.app.state.platform
    vault = tmp_path / "vault"
    vault.mkdir()
    tool = RC.MemoryBaseAddTool(platform)
    res = await tool.execute({"kind": "folder", "name": "Vault", "folder": str(vault)}, _ctx(platform, tmp_path))
    assert res.ok, res.error
    assert K.ADDED_ANSWERING in res.output
    assert "It is searched from the next message, with no restart." in res.output
    # The route's words for a second base of the same shape are the same sentence.
    other = tmp_path / "other"
    other.mkdir()
    page = client.post("/ltm/sources", json={"name": "other", "kind": "markdown", "path": str(other)}).json()
    assert page["words"] in res.output


@pytest.mark.asyncio
async def test_chats_memory_base_add_never_claims_a_search_for_a_base_that_does_not_answer(
    client, tmp_path, monkeypatch
):
    """A folder that passes the tool's own checks but whose base does not
    answer on read-back: the tool says the read-back's words and NOT "it is
    searched from the next message"."""
    platform = client.app.state.platform
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(K, "_base_health", lambda *a, **k: {"available": False, "detail": "it did not open"})
    tool = RC.MemoryBaseAddTool(platform)
    res = await tool.execute({"kind": "folder", "name": "Quiet", "folder": str(vault)}, _ctx(platform, tmp_path))
    assert res.ok, res.error
    assert "Added, but it is not answering: it did not open." in res.output
    assert "searched from the next message" not in res.output


# --------------------------------------------------------------------------- #
# The two boot-time rows: a restart, and the one door named
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["obsidian_vault", "notion_database_id"])
def test_the_boot_time_memory_rows_say_restart_and_name_the_memory_pages_door(key):
    row = next(s for s in S.SETTINGS if s.key == key)
    assert row.restart is True, f"{key} is read at boot only (platform.build_platform)"
    assert "Add a memory base" in row.help and "restart" in row.help.lower()
    assert "Memory page" in row.help
    assert row.section == "Memory bases"
