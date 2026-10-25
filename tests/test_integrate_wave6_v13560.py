"""v1.356.0 integrate (product-readiness Wave 6): the lanes joined.

G78 end to end — a REAL pack (the in-process stand-in server that refuses any
token but its own, ``tests/fixtures/github_http_mcp_v13550.py``) switched off
through ``PATCH /mcp/servers/{name} {enabled:false}``:

* loses its tools from the registry, from chat arming and from an agent with
  ``mcp:*``; keeps its config row and its vault secret;
* reads "off" — never "failed" / "not working" / "not started" — on EVERY
  surface that describes it: the Tools row (``GET /mcp/servers``), the
  Directory card (``GET /connectors``, lane 6D's service row carrying lane
  6A's ``enabled``), the chat's grounding section (``app_names.app_states``),
  the self-care readers (``pack_status`` words, ``app_health``'s
  diagnostics), the Status map and the doctor;
* the chat's ``app_connect`` on it never asks for the token again and puts up
  no card;
* on again, it reloads with the stored token (seen on the wire), nothing typed.

The real daemon's rows for that off pack are written to
``dashboard/__tests__/fixtures/wave6-seams-v13560.json`` (regenerate with
``IJ_REGEN_WAVE6_SEAMS=1``) and fed to the REAL ToolsPage and DirectoryPage
by ``dashboard/__tests__/wave6-seams-v13560.test.tsx``.

Wave-5 follow-up (1): an import row the preview says is "already added as X"
that the user ticks anyway is skipped with those words (or, with replace,
replaces X) — never a second pack under the new spelling.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import app_names
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _resolve_connectors
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.settings import records
from iron_jarvis.tools import selfcare
from tests.fixtures.github_http_mcp_v13550 import GITHUB_TOOLS, FakeGitHub

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave6-seams-v13560.json"
TOKEN = "xoxb-WAVE6-integrate-0123456789-NEVERSHOWN"
NAME = "slack"  # a Directory catalog id, so its card speaks for this pack
NO_SUCH = "ij-no-such-launcher-v13560"


@pytest.fixture(autouse=True)
def _fresh_records():
    """Load records and live clients are process-wide (the v1.325.2 lesson)."""
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    for name in (NAME, "brave_search", "brave-search"):
        mcp_tools_mod.forget_live_client(name)


@pytest.fixture
def server():
    with FakeGitHub(TOKEN) as gh:
        yield gh


@pytest.fixture
def client(tmp_path, server):
    c = TestClient(create_app(str(tmp_path)))
    try:
        yield c
    finally:
        for name in (NAME, "brave_search", "brave-search"):
            mcp_tools_mod.unload_pack_tools(c.app.state.platform.registry, name)


def _add(client: TestClient, server: FakeGitHub) -> None:
    res = client.post(
        "/mcp/servers",
        json={"name": NAME, "transport": "http", "url": server.url, "auth": {"value": TOKEN}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["tools_loaded"] == len(GITHUB_TOOLS)


def _tools_row(client: TestClient) -> dict:
    return next(r for r in client.get("/mcp/servers").json()["servers"] if r["name"] == NAME)


def _card(client: TestClient) -> dict:
    return next(r for r in client.get("/connectors").json()["connectors"] if r["id"] == NAME)


def _entry(platform) -> dict | None:
    return next((s for s in platform.config.mcp_servers or [] if s.get("name") == NAME), None)


def _chat_tools(platform) -> list[str]:
    tools, _memory = _resolve_connectors(SimpleNamespace(platform=platform), SimpleNamespace(connectors=[NAME]))
    return tools


def _project(row: dict, drop: tuple[str, ...]) -> dict:
    """A row with the keys that change per run (the stand-in's port, this
    PC's launcher) left out — the dashboard contract is the rest."""
    return {k: v for k, v in row.items() if k not in drop}


def test_a_switched_off_pack_reads_off_on_every_surface_and_on_again_needs_no_token(client, server):
    p = client.app.state.platform
    d = client.app.state.d
    _add(client, server)
    assert _chat_tools(p), "on: chat arms its tools"

    res = client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text

    # --- gone from every running surface; nothing lost --------------------
    assert p.registry.mcp_names(NAME) == []
    assert [s["name"] for s in p.registry.specs(["mcp:*"])] == []
    assert _chat_tools(p) == []
    entry = _entry(p)
    assert entry is not None and entry.get("enabled") is False, "the config row is kept, switched off"
    assert entry.get("auth", {}).get("secret"), "the vault reference is kept"
    assert p.secrets.get(entry["auth"]["secret"]) == TOKEN, "the vault secret is kept"

    # --- the Tools row (6A) ------------------------------------------------
    row = _tools_row(client)
    assert row["enabled"] is False and row["state"] == "off" and row["tools_loaded"] == 0
    assert not row.get("last_error")

    # --- the Directory card: 6D's service row carries 6A's switch ---------
    card = _card(client)
    assert card["enabled"] is False
    assert card["status"] == "off", "never failed / no_tools for a pack the user switched off"
    assert card["connected"] is True, "still a saved connection"
    assert card["detail"] == mcp_tools_mod.PACK_OFF_REASON

    # --- the chat's grounding section --------------------------------------
    live = app_names.live_tools(p, [NAME])
    (state,) = app_names.app_states(p, [NAME], live)
    assert state["state"] == "off"
    section = app_names.apps_section([state])
    assert "switched off by the user" in section
    assert "not working" not in section and "not connected" not in section

    # --- self-care: pack_status words and app_health's diagnostics ---------
    words = selfcare.pack_words(selfcare.pack_status_row(row))
    assert words == f"{NAME}: switched off by the user; turn it on in {selfcare.TOOLS_PAGE}."
    snap = selfcare.read_diagnostics(d)
    (diag,) = [s for s in snap["mcp_servers"] if s["name"] == NAME]
    assert diag["state"] == "off", "app_health reads 'off', never 'never tried'"
    summary = selfcare.AppHealthTool._packs_words(
        [{"name": NAME, "state": "off", "tools_loaded": 0, "reason": "", "fix": ""}]
    )
    # Readiness 12A (moved on purpose): "Extensions", never the retired "Packs".
    assert summary.startswith("Extensions: 1 switched off.") and "not started" not in summary

    # --- the Status map and the doctor count it apart ----------------------
    smap = client.get("/ui/system-map").json()
    tools_star = next(s for a in smap["areas"] for s in a["nodes"] if s["key"] == "tools")
    # v1.360.0 (lane 10C, G76; moved on purpose): the canon's "extension".
    assert tools_star["detail"].startswith("0 of 0 extensions running (1 switched off)")
    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    check = doctor.check_mcp(p)
    assert check["ok"] is True
    assert check["detail"] == "all 1 MCP server switched off.", "never 'all 1 ... started'"

    # --- the chat's connect door never asks for the token again -----------
    tool = records.AppConnectTool(p)
    verdict = asyncio.run(tool.spend_check({"app": NAME}, None))
    assert verdict is not None and verdict.ask is False, "no card for a call that changes nothing"
    out = asyncio.run(tool.execute({"app": NAME}, None))
    assert out.ok is False, "nothing was connected, so the page must not turn it on"
    assert "switched off" in (out.error or "") and "Never ask for its token again" in (out.error or "")
    assert not (out.data or {}).get("secret_requests")
    assert _entry(p).get("enabled") is False, "the call changed nothing"

    # --- the dashboard contract: the REAL rows for the off pack ------------
    contract = {
        "tools_row": _project(row, ("url", "command", "args")),
        "directory_card": _project(card, ("runtime_ready", "runtime", "runtime_url")),
    }
    if os.environ.get("IJ_REGEN_WAVE6_SEAMS") == "1":
        CONTRACT.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    on_disk = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(contract)), (
        "the dashboard's wave-6 contract drifted from the daemon; regenerate with IJ_REGEN_WAVE6_SEAMS=1"
    )

    # --- on again: the stored token reaches the server, nothing typed -----
    seen_before = len(server.seen)
    res = client.patch(f"/mcp/servers/{NAME}", json={"enabled": True})
    assert res.status_code == 200, res.text
    assert res.json()["tools_loaded"] == len(GITHUB_TOOLS)
    assert len(_chat_tools(p)) == len(GITHUB_TOOLS)
    after = server.seen[seen_before:]
    assert after and all(r["headers"].get("authorization") == f"Bearer {TOKEN}" for r in after)
    assert _card(client)["status"] == "connected" and _card(client)["enabled"] is True


def test_an_import_its_app_had_switched_off_lands_off_and_reads_off_on_the_card(client, server):
    """The import door's half of G78: a ticked server its app had switched
    off is added switched off, and the Directory card says so too."""
    paste = json.dumps(
        {
            "mcpServers": {
                NAME: {"url": server.url, "headers": {"Authorization": f"Bearer {TOKEN}"}, "disabled": True}
            }
        }
    )
    preview = client.post("/mcp/import/preview", json={"text": paste}).json()["servers"]
    assert [r["disabled"] for r in preview] == [True]
    seen_before = len(server.seen)
    res = client.post("/mcp/import/apply", json={"text": paste, "names": [NAME]})
    assert res.status_code == 200, res.text
    assert [(a["name"], a["state"]) for a in res.json()["added"]] == [(NAME, "off")]
    assert len(server.seen) == seen_before, "a switched-off pack is never started"
    assert _tools_row(client)["state"] == "off"
    assert _card(client)["status"] == "off"


# --------------------------------------------------------------------------- #
# Wave-5 follow-up (1): "already added as X" is never a silent second pack.
# --------------------------------------------------------------------------- #
def _stored_names(client: TestClient) -> list[str]:
    return sorted(str(s.get("name")) for s in client.app.state.platform.config.mcp_servers or [])


def test_a_ticked_row_already_added_under_another_spelling_is_skipped_with_that_name(client):
    pkg = "@example/brave-search-v13560"
    assert client.post(
        "/mcp/servers", json={"name": "brave_search", "command": NO_SUCH, "args": ["-y", pkg]}
    ).status_code == 200
    paste = json.dumps({"mcpServers": {"brave-search": {"command": NO_SUCH, "args": ["-y", pkg]}}})
    (row,) = client.post("/mcp/import/preview", json={"text": paste}).json()["servers"]
    assert row["exists"] is True and row["exists_name"] == "brave_search"

    res = client.post("/mcp/import/apply", json={"text": paste, "names": ["brave-search"]})
    assert res.status_code == 200, res.text
    assert res.json() == {"added": [], "skipped": [{"name": "brave-search", "why": "already added as brave_search"}]}
    assert _stored_names(client) == ["brave_search"], "no second pack under the new spelling"


def test_replace_swaps_the_other_spelling_for_the_imported_one(client):
    pkg = "@example/brave-search-v13560"
    assert client.post(
        "/mcp/servers", json={"name": "brave_search", "command": NO_SUCH, "args": ["-y", pkg]}
    ).status_code == 200
    paste = json.dumps({"mcpServers": {"brave-search": {"command": NO_SUCH, "args": ["-y", pkg]}}})
    res = client.post("/mcp/import/apply", json={"text": paste, "names": ["brave-search"], "replace": True})
    assert res.status_code == 200, res.text
    assert [a["name"] for a in res.json()["added"]] == ["brave-search"]
    assert _stored_names(client) == ["brave-search"], "replaced, never both"


def test_a_different_server_under_a_similar_name_is_still_added(client):
    """Control: the guard keys on the SAME server, not a similar name."""
    assert client.post(
        "/mcp/servers", json={"name": "brave_search", "command": NO_SUCH, "args": ["-y", "@example/one"]}
    ).status_code == 200
    paste = json.dumps({"mcpServers": {"brave-search": {"command": NO_SUCH, "args": ["-y", "@example/two"]}}})
    res = client.post("/mcp/import/apply", json={"text": paste, "names": ["brave-search"]})
    assert res.status_code == 200, res.text
    assert [a["name"] for a in res.json()["added"]] == ["brave-search"]
    assert _stored_names(client) == ["brave-search", "brave_search"]
