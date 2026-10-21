"""v1.364.0 (IJ harness Phase 1): ``mcp_list_resources`` /
``mcp_read_resource`` — the resources a connected app offers (Claude Code's
ListMcpResourcesTool / ReadMcpResourceTool, Codex's list_mcp_resources /
read_mcp_resource).

Built on the existing machinery (``mcp.tools.live_clients`` — the same live
clients the chat's "@" picker reads, v1.324.0): every call bounded, one app
that fails is NAMED and never fails the listing, a read is cut to 20,000
characters and marked untrusted (third-party content is fenced and scanned by
the runtime), and the read is a call that REACHES OUTSIDE (a local-only turn
cards it: providers/egress.READS_APP_RESOURCES_TOOLS).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import tools as T
from iron_jarvis.mcp import tools as mcp_tools
from iron_jarvis.providers import egress
from iron_jarvis.tools.base import ToolContext


class _Client:
    def __init__(self, rows=None, contents=None, fail=None, slow=False):
        self.rows = rows or []
        self.contents = contents
        self.fail = fail
        self.slow = slow
        self.read_uris: list[str] = []

    async def list_resources(self):
        if self.slow:
            await asyncio.sleep(60)
        if self.fail:
            raise self.fail
        return self.rows

    async def read_resource(self, uri):
        self.read_uris.append(uri)
        if self.fail:
            raise self.fail
        return self.contents


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r",
        config=SimpleNamespace(home=tmp_path), event_bus=None, engine=None,
    )


@pytest.fixture
def clients(monkeypatch):
    holder: dict[str, object] = {}
    monkeypatch.setattr(mcp_tools, "live_clients", lambda: dict(holder))
    return holder


async def test_list_names_each_app_and_the_ones_that_failed(tmp_path, clients, monkeypatch):
    monkeypatch.setattr(T, "RESOURCE_LIST_TIMEOUT_S", 0.2)
    clients["notes"] = _Client(rows=[{"uri": "note://1", "name": "Groceries", "mimeType": "text/plain"}])
    clients["empty"] = _Client(fail=RuntimeError("-32601 Method not found"))
    clients["down"] = _Client(fail=ConnectionError("refused"))
    clients["stuck"] = _Client(slow=True)
    res = await T.McpListResourcesTool().execute({}, _ctx(tmp_path))
    assert res.ok
    assert res.output.startswith("notes: note://1 — Groceries (text/plain)")
    assert "[not listed: down could not be reached; stuck took too long to answer]" in res.output
    assert res.data == {"count": 1, "shown": 1, "failed": 2}


async def test_list_one_app_and_the_cap(tmp_path, clients, monkeypatch):
    monkeypatch.setattr(T, "MAX_RESOURCE_ROWS", 3)
    clients["big"] = _Client(rows=[{"uri": f"r://{i}"} for i in range(5)])
    res = await T.McpListResourcesTool().execute({"server": "big"}, _ctx(tmp_path))
    assert res.output.count("big: r://") == 3
    assert "[2 more not shown" in res.output
    missing = await T.McpListResourcesTool().execute({"server": "nope"}, _ctx(tmp_path))
    assert missing.ok is False and "no connected app named 'nope'" in missing.error


async def test_no_apps_running_is_an_answer(tmp_path, clients):
    res = await T.McpListResourcesTool().execute({}, _ctx(tmp_path))
    assert res.ok and res.output == "No connected apps are running."


async def test_read_returns_the_text_cut_and_marked(tmp_path, clients, monkeypatch):
    monkeypatch.setattr(T, "RESOURCE_CHARS", 10)
    clients["notes"] = _Client(contents={"contents": [
        {"uri": "note://1", "text": "0123456789ABCDEF"},
        {"uri": "note://1#img", "blob": "AAAA", "mimeType": "image/png"},
    ]})
    res = await T.McpReadResourceTool().execute({"server": "notes", "uri": "note://1"}, _ctx(tmp_path))
    assert res.ok
    assert res.output.startswith("0123456789\n\n[cut: the first 10 of")
    assert res.data["truncated"] is True
    assert clients["notes"].read_uris == ["note://1"]
    assert T.McpReadResourceTool.returns_untrusted_content is True


async def test_read_failures_are_named(tmp_path, clients):
    clients["down"] = _Client(fail=ConnectionError("refused"))
    res = await T.McpReadResourceTool().execute({"server": "down", "uri": "x://1"}, _ctx(tmp_path))
    assert res.ok is False and res.error == "down could not send x://1: ConnectionError"
    nope = await T.McpReadResourceTool().execute({"server": "nope", "uri": "x://1"}, _ctx(tmp_path))
    assert nope.ok is False and "no connected app named 'nope'" in nope.error
    blank = await T.McpReadResourceTool().execute({"server": "down", "uri": " "}, _ctx(tmp_path))
    assert blank.ok is False and "`uri` is required" in blank.error


def test_the_read_reaches_outside_and_the_list_does_not():
    """MUTATION: drop mcp_read_resource from READS_APP_RESOURCES_TOOLS -> a
    local-only turn would run it without a card -> red."""
    assert egress.reaches_outside(T.McpReadResourceTool()) is True
    assert egress.reaches_outside(T.McpListResourcesTool()) is False
