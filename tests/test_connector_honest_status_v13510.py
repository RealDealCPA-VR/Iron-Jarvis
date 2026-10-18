"""v1.351.0, wave 1 lane D (G08): connect doors look green only when the pack works.

The daemon half. A Directory connect whose pack did not start used to answer
"saved — restart the daemon (or check the command is installed)", and a
saved pack with zero tools carried a detail telling the user to restart,
right beside a Retry that reloads the pack in place. Now:

* a connect that loaded nothing answers "Saved, but it didn't start. Press
  Retry." (no restart, no "daemon");
* the gallery row of that pack says ``status: "failed"`` with the plain
  reason (the Directory card turns that into an amber "Needs attention");
* a saved pack with zero tools and no record says "Saved, but it isn't
  answering yet. Press Test, then Retry.";
* a memory source that is not live says so without the word "daemon".

Driven through the REAL app factory and the REAL connect route. "A PC without
Node" is the one seam replaced: the launcher lookup (``terminals.ai_clis.
_find``, which ``mcp.tools.resolve_launcher`` calls) finds nothing, exactly as
on a machine where ``npx`` is not installed. No process is started.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.mcp.tools as mcp_tools
from iron_jarvis.connectors import service
from iron_jarvis.daemon.app import create_app


@pytest.fixture
def clean_load_status():
    """The load record is process-wide; keep this file's plants to itself."""
    saved = dict(mcp_tools._LOAD_STATUS)
    saved_announced = set(mcp_tools._ANNOUNCED)
    mcp_tools._LOAD_STATUS.clear()
    mcp_tools._ANNOUNCED.clear()
    try:
        yield
    finally:
        mcp_tools._LOAD_STATUS.clear()
        mcp_tools._LOAD_STATUS.update(saved)
        mcp_tools._ANNOUNCED.clear()
        mcp_tools._ANNOUNCED.update(saved_announced)


@pytest.fixture
def no_node(monkeypatch):
    """A PC without Node: no launcher resolves (PATH or the usual folders)."""
    from iron_jarvis.terminals import ai_clis

    monkeypatch.setattr(ai_clis, "_find", lambda _cmd: None)


def _row(client: TestClient, cid: str) -> dict:
    rows = client.get("/connectors").json()["connectors"]
    return next(r for r in rows if r["id"] == cid)


def test_connecting_slack_on_a_pc_without_node_is_failed_with_its_reason(
    tmp_path, clean_load_status, no_node
):
    client = TestClient(create_app(str(tmp_path)))
    res = client.post(
        "/connectors/slack/connect",
        json={"values": {"SLACK_BOT_TOKEN": "xoxb-not-a-real-token", "SLACK_TEAM_ID": "T123"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tools_loaded"] == 0
    # The note names the card's own next step, never a restart.
    assert body["note"] == "Saved, but it didn't start. Press Retry."
    assert "daemon" not in body["note"].lower() and "restart" not in body["note"].lower()

    row = _row(client, "slack")
    # `connected` stays true on purpose (the connection is saved and survives
    # restarts); the truth the card's colour reads is `status`.
    assert row["connected"] is True
    assert row["status"] == "failed", row
    assert row["tools_loaded"] == 0
    # The reason the card shows beside its Retry: the missing launcher.
    why = f"{row.get('reason') or ''} {row.get('detail') or ''}".lower()
    assert "npx" in why or "node" in why, row
    # The token never rides the gallery row.
    assert "xoxb-not-a-real-token" not in str(row)


def test_a_saved_pack_with_no_tools_and_no_record_says_test_then_retry(tmp_path, clean_load_status):
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.config.mcp_servers = [{"name": "quiet", "command": "x"}]
    row = _row(client, "quiet")
    assert row["status"] == "no_tools"
    assert row["detail"] == "Saved, but it isn't answering yet. Press Test, then Retry."
    assert row["detail"] == service.NO_TOOLS_DETAIL


def test_a_working_pack_is_connected(tmp_path, clean_load_status):
    """Control: a configured pack with tools stays the green 'connected'."""
    st = service._mcp_status(True, 3, "fine")
    assert st == {"connected": True, "status": "connected", "detail": ""}


def test_no_connector_note_says_daemon():
    for words in (service.NO_TOOLS_DETAIL, service.DID_NOT_START_NOTE, service.MEMORY_NOT_LIVE_NOTE):
        assert "daemon" not in words.lower(), words
    # A memory source loads at start-up and has no Retry: a restart IS its
    # fix, said in the app's own name.
    assert "restart Iron Jarvis" in service.MEMORY_NOT_LIVE_NOTE
