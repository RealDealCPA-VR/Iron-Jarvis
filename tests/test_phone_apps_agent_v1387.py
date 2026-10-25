"""Readiness 12B review (G39): from the phone, an AGENT can use a connected
app — and the phone's model and its user are told so.

The lane's Option B told the phone's model a connected app's tools could be
used "only from Chat in Iron Jarvis on the PC" and to "say in a few words that
you can't reach it from here", and put "To have me use it, ask in Chat in Iron
Jarvis on your PC" under the reply. That is not true: the phone's own quick
reply arms no app tool (right — the POST lane grants what it arms and cannot
show a card), but its ``escalate_to_agent`` exit hands the request to an
agent session started FROM the phone (origin ``comm:<channel>``, low trust),
whose roster holds every connected app's tools (``mcp:*``) and whose asks
reach the phone (``ASKING_ORIGINS``, the v1.200.0 approve/deny prompt). The
lane's words steered the model away from that working path — a capability
the phone had at v1.386.2, when a model with "GitHub: connected (1 tool)" and
no GitHub tool would hand the work over ("a tool you have not been given").

Driven through the REAL inbound phone path, the REAL escalation, the REAL
agent runtime, the REAL approval fan-out to the phone and a REAL local MCP
child standing in for GitHub on EVERY binding of the catalog lookup (the
network guard is on); only the model is scripted.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys

import pytest
from sqlmodel import select

import iron_jarvis.connectors as connectors_pkg
from iron_jarvis.connectors import catalog, service
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.mcp.tools import unload_pack_tools
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from tests.test_phone_apps_honest_v1387 import (
    FIXTURE,
    NOTE,
    PHONE_LINE,
    PREFIX,
    SENDER,
    FakeTelegram,
    _connected_github,
    _phone,
    _section,
    _tool_names,
    _update,
)

pytestmark = pytest.mark.usefixtures("no_network")

GH_TOOL = "mcp__github__argv"


@pytest.fixture
def local_github(monkeypatch):
    real = catalog.get_connector("github")
    swapped = dataclasses.replace(real, command=sys.executable, args=[FIXTURE], transport="stdio", url="", auth_field="")
    original = catalog.get_connector
    for mod in (catalog, service, connectors_pkg):
        monkeypatch.setattr(mod, "get_connector", lambda c: swapped if c == "github" else original(c))
    return swapped


def _script(p, monkeypatch, *, escalate: bool):
    """The phone's quick reply hands the work to the builder (or answers);
    the builder calls GitHub's tool once, then reports what it got."""
    seen: dict = {"phone_tools": None, "phone_system": ""}

    async def fake_complete(*, system, messages, tools, **kw):
        names = _tool_names(tools)
        if "escalate_to_agent" in names:  # the phone's quick reply (POST lane)
            seen["phone_tools"] = names
            seen["phone_system"] = system
            if escalate:
                return RouteResult(LLMResponse(text="", tool_calls=[ToolCall(
                    id="e1", name="escalate_to_agent", arguments={"reason": "needs GitHub", "agent": "builder"},
                )]), "mock", "mock")
            return RouteResult(LLMResponse(text="I can't check that in this reply."), "mock", "mock")
        outs = [str(getattr(m, "content", "")) for m in messages if getattr(m, "role", "") == "tool"]
        if GH_TOOL in names and not outs:
            return RouteResult(LLMResponse(text="", tool_calls=[ToolCall(id="g1", name=GH_TOOL, arguments={})]), "mock", "mock")
        return RouteResult(LLMResponse(text="DONE: " + (" | ".join(outs)[:200] if outs else "no tool")), "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        r = await fake_complete(system=system, messages=messages, tools=tools)
        if r.response.text:
            yield {"type": "text", "text": r.response.text}
        yield {"type": "final", "response": r.response, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(p.router, "complete", fake_complete)
    monkeypatch.setattr(p.router, "stream", fake_stream)
    return seen


async def _until(pred, seconds: float = 30.0) -> bool:
    for _ in range(int(seconds / 0.05)):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


def _gh_calls(p) -> list[tuple[bool, str]]:
    with session_scope(p.engine) as db:
        rows = db.exec(select(ToolInvocation).where(ToolInvocation.tool == GH_TOOL)).all()
        out = []
        for r in rows:
            sess = db.get(SessionRow, r.session_id) if r.session_id else None
            out.append((bool(r.ok), str(getattr(sess, "origin", "") or "")))
        return out


@pytest.mark.timeout(120)
async def test_the_phones_model_is_told_an_agent_can_use_the_app(tmp_path, monkeypatch, local_github):
    app, p = await _connected_github(tmp_path)
    try:
        fake = FakeTelegram()
        _phone(p, fake)
        seen = _script(p, monkeypatch, escalate=False)
        fake.updates.append(_update(1, "list my open PRs on GitHub"))
        await app.state.inbound_poller.poll_once()
        section = _section(seen["phone_system"])
        assert PHONE_LINE in section, section
        assert "escalate_to_agent" in section and "before each use of GitHub" in section, section
        # Its quick reply still holds none of GitHub's tools (nothing is
        # granted from the phone without a person).
        assert not [t for t in seen["phone_tools"] if t.startswith("mcp__github__")]
        # A reply that did not hand it over says both real ways.
        assert fake.texts() == [PREFIX + "I can't check that in this reply." + NOTE], fake.texts()
    finally:
        unload_pack_tools(p.registry, "github")


@pytest.mark.timeout(120)
@pytest.mark.parametrize("answer, ran", [("approve", True), ("deny", False)])
async def test_the_agent_the_phone_handed_it_to_asks_the_phone_before_each_use(
    tmp_path, monkeypatch, local_github, answer, ran,
):
    app, p = await _connected_github(tmp_path)
    try:
        fake = FakeTelegram()
        _phone(p, fake)
        app.state.comm_thread_store.resolve("tg", str(SENDER), "V")
        _script(p, monkeypatch, escalate=True)
        poller = app.state.inbound_poller
        fake.updates.append(_update(1, "list my open PRs on GitHub"))
        res = await poller.poll_once()
        assert [r.get("status") for r in res] == ["chat_escalated"], res
        # The agent paused on GitHub's tool and ASKED THE PHONE — nothing ran yet.
        assert await _until(lambda: any("approve" in t.lower() and GH_TOOL in t for t in fake.texts())), fake.texts()
        assert _gh_calls(p) == []
        fake.updates.append(_update(2, answer))
        res = await poller.poll_once()
        assert [r.get("status") for r in res] == ["approval_answered"], res
        assert await _until(lambda: any(t.startswith(PREFIX + "DONE: ") for t in fake.texts())), fake.texts()
        await poller.drain()
        calls = _gh_calls(p)
        assert [ok for ok, _o in calls] == ([True] if ran else [False]), calls
        assert all(o.startswith("comm:") for _ok, o in calls), calls
    finally:
        unload_pack_tools(p.registry, "github")


def test_the_section_mentions_the_hand_off_only_on_the_phone():
    from iron_jarvis.connectors.app_names import apps_section

    rows = [{"id": "github", "name": "GitHub", "state": "connected", "tools": 1, "needs": "", "note": ""}]
    assert "escalate_to_agent" not in apps_section(rows)
    phone = apps_section(rows, phone_own=["github"])
    assert "escalate_to_agent" in phone and "Never guess what is in it" in phone
