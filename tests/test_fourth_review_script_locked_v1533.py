"""Fourth review PG3: a script never runs a tool that asks before each use.

A LOCKED app's tools promise "Jarvis asks you before each use of it". Owner
decision 8 (v1.529.0) made the gate read that from the lock itself
(``tools.permissions.asks_each_use``), not from "a locked app is also kept on
your own models". One caller did not: a tool script (``tool_script``) held
back only ``quarantined`` tools, and its gate call handed the registry the
chat's armed names as ``session_allow`` with no ``card_allow``. With a locked
app switched on for a chat (each of its tools armed by name), a script could
call the app's READ tool and it ran with no card, as soon as "locked" and
"kept on your own models" come apart (latent today: the catalog ties them).

Here the app is locked and NOT kept on the user's own models, in a chat that
is not Local only, its tools armed the way a chat lane arms them:

* a script that calls its read tool is refused in words, the app is never
  reached, and the script's own ledger row carries the refusal;
* if the script's own check ever drifts, the GATE still refuses it (the
  script hands it ``card_allow=frozenset()``: a script is never a card);
* the same tool called directly from chat still gets its card (control);
* an ordinary app's read tool still runs from a script (control).

Through the REAL registry, gate, ``ToolScriptTool``, isolated script worker,
stream lane, approvals and ledger. Only the model and the apps' transports
are doubles (each tool's own ``execute`` runs).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlmodel import select

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import asks_each_use
from iron_jarvis.tools.tool_script import NAME as SCRIPT, ToolScriptTool
from tests.test_locked_app_lanes_v1529 import LREAD, _body, _calls

pytestmark = pytest.mark.timeout(180)

PLAIN = "notesapp"
PREAD = f"mcp__{PLAIN}__list_notes"  # an ordinary app's read tool


class _Client:
    """An app's transport: records every call that reached the app."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(self, name, args):
        self.calls.append(name)
        return {"content": [{"type": "text", "text": f"{name} answered"}]}


def _app_read(platform, app: str, remote: str, *, locked: bool) -> _Client:
    client = _Client()
    spec = {"name": remote, "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True}}
    tool = MCPRemoteTool.from_spec(client, app, spec)
    tool.locked = locked  # what mcp/tools.mcp_tools sets for a locked row
    assert tool.local_only is False  # NOT kept on the user's own models
    platform.registry.register(tool, mcp=True)
    return client


@pytest.fixture()
def world(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    locked = _app_read(platform, "mailbox", "list_mail", locked=True)
    plain = _app_read(platform, PLAIN, "list_notes", locked=False)
    assert asks_each_use(platform.registry.get(LREAD)) is True
    assert asks_each_use(platform.registry.get(PREAD)) is False
    ws = tmp_path / "work"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", thread_id="chat_one",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    yield SimpleNamespace(platform=platform, ctx=ctx, locked=locked, plain=plain)
    asyncio.run(platform.repl.dispose_all())


def _chat_armed(*names: str) -> dict:
    """What a chat lane (Local only off) hands the gate with both apps
    switched on: each app tool armed by name (an ``allow`` override and a
    session grant), the shared key, and the script tool itself."""
    armed = {*names, "mcp_call"}
    return {
        "agent_overrides": {n: "allow" for n in armed},
        "session_allow": set(armed),
        "allowed_names": {*armed, SCRIPT},
    }


def _script(w, code: str):
    async def go():
        return await w.platform.registry.invoke(
            SCRIPT, {"code": code}, w.ctx, w.platform.permissions, **_chat_armed(LREAD, PREAD),
        )

    return asyncio.run(go())


def _rows(platform, name: str) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == name]
        for r in rows:
            db.expunge(r)
        return rows


_CALL = (
    "print('offered:', '{name}' in tools.names)\n"
    "try:\n"
    "    r = tools.call('{name}')\n"
    "    print('RAN', r.output)\n"
    "except ToolError as e:\n"
    "    print('refused:', e)\n"
)


def test_a_script_cannot_run_a_locked_apps_read_tool(world):
    r = _script(world, _CALL.format(name=LREAD))
    assert r.ok, r.error
    assert world.locked.calls == [], "a script ran a locked app's tool with no card"
    assert "RAN" not in r.output, r.output
    # Never offered to the script, and refused in words that send the model
    # to call it directly (where the user is asked).
    assert "offered: False" in r.output, r.output
    words = f"`{LREAD}` asks the user before each use; call it directly so they can allow it"
    assert words in r.output, r.output
    assert r.data["refused"] == [LREAD]
    # The script's own ledger row carries the refusal; the app's tool has no
    # row that ran.
    (row,) = _rows(world.platform, SCRIPT)
    assert words in row.output, row.output
    assert [x for x in _rows(world.platform, LREAD) if x.ok] == []


def test_if_the_scripts_own_check_drifts_the_gate_still_refuses_it(world, monkeypatch):
    """The script's own refusal is gone (a drift); the gate itself must not
    read the chat's armed names as an answer about this tool."""

    async def _no_refusal(self, name, args, scope, ctx):
        return ""

    monkeypatch.setattr(ToolScriptTool, "_refusal", _no_refusal)
    r = _script(world, _CALL.format(name=LREAD))
    assert world.locked.calls == [], "the gate lifted a locked app's tool for a script"
    assert "RAN" not in r.output, r.output
    rows = _rows(world.platform, LREAD)
    assert rows and not any(x.ok for x in rows), [(x.ok, x.output) for x in rows]


def test_an_ordinary_apps_read_tool_still_runs_from_a_script(world):
    r = _script(world, _CALL.format(name=PREAD))
    assert r.ok, r.error
    assert "offered: True" in r.output, r.output
    assert "RAN list_notes answered" in r.output, r.output
    assert world.plain.calls == ["list_notes"]
    assert [x.ok for x in _rows(world.platform, PREAD)] == [True]


async def test_the_same_tool_called_directly_from_chat_still_gets_its_card(world, monkeypatch):
    from tests.test_third_plan_g17_gate import Model, _stream

    model = Model([_calls(LREAD, "m1")])
    monkeypatch.setattr(world.platform.router, "stream", model.stream)
    monkeypatch.setattr(world.platform.router, "complete", model.complete)
    out = await _stream(world.platform, _body(), ["once"])
    assert [c["tool"] for c in out["cards"]] == [LREAD], out["cards"]
    assert out["cards"][0].get("once_only") is True
    assert world.locked.calls == ["list_mail"], "Allow once on its card runs it"
